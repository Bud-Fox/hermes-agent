import pytest

from hermes_cli.fleet_catalog_adapters import (
    AdapterIO,
    AppliedChange,
    ContaboOpenAIAdapter,
    CodexPoolAdapter,
    PoolAdapter,
    PoolOrchestrator,
    PreparedChange,
)


class FakeIO(AdapterIO):
    def __init__(self, models=(), probe=None):
        self.models = list(models)
        self.probe = probe or (lambda pool, model, kind: True)
        self.commands = []
        self.files = {}
        self.file_modes = {}
        self.catalog = b"version: 1\nproviders:\n  openai-codex:\n    models: []\n"
        self.catalog_writes = 0

    def provider_model_ids(self, pool):
        return list(self.models)

    def validate_requested_model(self, model, pool):
        return {"accepted": model in self.models}

    def probe_model(self, pool, model, kind):
        return self.probe(pool, model, kind)

    def run(self, command, *, cwd=None, timeout=60):
        self.commands.append((tuple(command), cwd, timeout))
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    def read_json(self, path):
        return self.files[str(path)]

    def write_json(self, path, value):
        self.files[str(path)] = value

    def read_bytes(self, path):
        return self.files[str(path)]

    def write_bytes(self, path, value, mode=None):
        self.files[str(path)] = value
        if mode is not None: self.file_modes[str(path)] = mode

    def file_mode(self, path):
        return self.file_modes.get(str(path), 0o640)

    def catalog_bytes(self):
        return self.catalog

    def write_catalog_bytes(self, value, mode=None):
        self.catalog_writes += 1
        self.catalog = value


def test_codex_catalog_hit_and_literal_model():
    io = FakeIO(models=["OpenAI/GPT.New-1"])
    change = CodexPoolAdapter().prepare_add("OpenAI/GPT.New-1", io=io)
    assert change.model == "OpenAI/GPT.New-1"
    assert change.metadata["availability"] == "listed"


def test_codex_listing_absence_uses_direct_streaming_and_canaries():
    seen = []
    io = FakeIO(probe=lambda pool, model, kind: seen.append((model, kind)) or True)
    change = CodexPoolAdapter().prepare_add("gpt-HIDDEN.X", io=io)
    assert change.metadata["availability"] == "hidden_but_live"
    assert [kind for _, kind in seen] == ["streaming", "text", "tool"]
    assert all(model == "gpt-HIDDEN.X" for model, _ in seen)


def test_codex_commit_only_mutates_pool_owned_state():
    original = b"version: 1\nproviders:\n  openai-codex:\n    models: [old]\n"
    io = FakeIO()
    io.catalog = original
    adapter = CodexPoolAdapter()
    change = PreparedChange("openai-codex", "Literal/X", {})
    applied = adapter.commit(change, io=io)
    assert io.catalog == original
    adapter.rollback(applied, io=io)
    assert io.catalog == original
    assert io.catalog_writes == 0


@pytest.mark.parametrize("failed", ["streaming", "text", "tool"])
def test_codex_rejects_unsupported_or_failed_canary(failed):
    io = FakeIO(probe=lambda pool, model, kind: kind != failed)
    with pytest.raises(RuntimeError, match=failed):
        CodexPoolAdapter().prepare_add("future.literal", io=io)


def test_contabo_prepare_runs_all_probes_and_commit_allowlisted_deploy(tmp_path):
    root = tmp_path / "contabo-router"
    io = FakeIO()
    original = b'{\n  "openai": {"models": [{"id":"old","name":"Old","supports_tools":true}]},\n  "anthropic": {"models": [{"id":"keep"}]}\n}\n'
    io.files[str(root / "app/models.json")] = original
    io.file_modes[str(root / "app/models.json")] = 0o640
    io.models = ["old"]
    adapter = ContaboOpenAIAdapter(router_root=root, deploy_script=root / "deploy_router.sh")
    prepared = adapter.prepare_add("New/Model.X", io=io)
    assert prepared.metadata["probes"] == ["text", "tool", "streaming", "reasoning"]
    applied = adapter.commit(prepared, io=io)
    entries = __import__('json').loads(io.files[str(root / "app/models.json")])["openai"]["models"]
    assert entries[-1] == {"id": "New/Model.X", "name": "New/Model.X", "supports_tools": True}
    assert set(entries[-1]) == {"id", "name", "supports_tools"}
    assert __import__('json').loads(io.files[str(root / "app/models.json")])["anthropic"] == {"models": [{"id": "keep"}]}
    assert io.commands[-1][0] == (str(root / "deploy_router.sh"),)
    adapter.rollback(applied, io=io)
    assert io.files[str(root / "app/models.json")] == original
    assert io.file_modes[str(root / "app/models.json")] == 0o640
    assert io.commands[-1][0] == (str(root / "deploy_router.sh"),)


def test_contabo_rejects_any_other_deploy_script(tmp_path):
    root = tmp_path / "contabo-router"
    with pytest.raises(ValueError, match="deploy_router.sh"):
        ContaboOpenAIAdapter(router_root=root, deploy_script=root / "other.sh")


class RecordingAdapter(PoolAdapter):
    def __init__(self, pool, events, fail=False):
        self.pool = pool
        self.events = events
        self.fail = fail

    def prepare_add(self, model, *, io):
        self.events.append(f"prepare:{self.pool}")
        return PreparedChange(self.pool, model, {})

    def commit(self, change, *, io):
        self.events.append(f"commit:{self.pool}")
        if self.fail:
            raise RuntimeError("boom")
        return AppliedChange(change, {})

    def rollback(self, applied, *, io):
        self.events.append(f"rollback:{self.pool}")


def test_orchestrator_compensates_reverse_order_on_partial_failure():
    events = []
    adapters = {
        "a": RecordingAdapter("a", events),
        "b": RecordingAdapter("b", events),
        "c": RecordingAdapter("c", events, fail=True),
    }
    prepared = PoolOrchestrator(adapters).prepare("literal", ["a", "b", "c"], io=FakeIO())
    with pytest.raises(RuntimeError, match="boom"):
        PoolOrchestrator(adapters).commit(prepared, io=FakeIO())
    assert events[-2:] == ["rollback:b", "rollback:a"]


def test_orchestrator_is_single_catalog_writer_and_writes_last():
    events = []
    io = FakeIO()
    io.catalog = b"version: 1\nproviders:\n  a:\n    models: []\n  b:\n    models: []\n"
    real_write = io.write_catalog_bytes
    io.write_catalog_bytes = lambda value, mode=None: (events.append("catalog"), real_write(value, mode))[1]
    adapters = {name: RecordingAdapter(name, events) for name in ("a", "b")}
    changes = PoolOrchestrator(adapters).prepare("literal", ["a", "b"], io=io)
    PoolOrchestrator(adapters).commit(changes, io=io)
    assert events[-3:] == ["commit:a", "commit:b", "catalog"]
    assert io.catalog_writes == 1
