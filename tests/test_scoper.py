from paper2code.agents.fake import fake_agent_responder
from paper2code.agents.scoper.fake import CANARY_DRAFT, fake_scoper_responder
from paper2code.agents.scoper.prompts import SCOPER_INSTRUCTIONS, render_scoper_input
from paper2code.agents.scoper.schemas import CLAIM_TEST_FILE, ScopeDraft, validate_draft
from paper2code.agents.scoper.scoper import ROLE_SCOPER, draft_scope
from paper2code.agents.scout.schemas import EligibilityBatch, Scorecard
from paper2code.arxiv.models import ArxivPaper
from paper2code.config import Config
from paper2code.llm.base import Usage
from paper2code.llm.fake import FakeChatModel

PAPER = ArxivPaper(arxiv_id="2610.00001", version=1, title="EMA Denoising", abstract="Smoothing helps.", authors=["A"], url="https://arxiv.org/abs/2610.00001")
CARD = {"arxiv_id": "2610.00001", "claim": "At reduced scale, EMA should beat identity on sine denoising by at least 50%.", "dataset": "synthetic, yes", "est_usd": 0.5, "testability": 5}


def test_instructions_cover_the_contract():
    for needle in ("test_claim.py", "NotImplementedError", "parametrize", "hidden", "untrusted", "seed"):
        assert needle in SCOPER_INSTRUCTIONS, needle


def test_render_scoper_input_carries_everything():
    text = render_scoper_input(PAPER, CARD, "FULL TEXT", budget_usd=10.0, min_seeds=3, allowed_packages=["numpy", "torch"], gpu_usd_per_hour=1.0)
    for needle in ("2610.00001", "EMA Denoising", "FULL TEXT", "10.0", "3", "numpy, torch", "beat identity", "BEGIN PAPER", "END PAPER"):
        assert needle in text, needle
    assert text.index("BEGIN PAPER") < text.index("FULL TEXT") < text.index("END PAPER")


def test_draft_scope_calls_scoper_role_and_accounts_usage():
    def responder(role, instructions, user, schema):
        assert role == ROLE_SCOPER and schema is ScopeDraft and instructions == SCOPER_INSTRUCTIONS
        assert "FULL TEXT" in user
        return CANARY_DRAFT

    usage = Usage()
    draft = draft_scope(PAPER, CARD, "FULL TEXT", FakeChatModel(responder), Config(), budget_usd=10.0, usage=usage)
    assert draft is CANARY_DRAFT and usage.calls == 1


def test_canary_draft_is_valid_and_matches_reference_interface():
    assert validate_draft(CANARY_DRAFT) == []
    assert CANARY_DRAFT.interface.module == "canary_method"
    sigs = [f.signature for f in CANARY_DRAFT.interface.functions]
    assert any(s.startswith("def ema(") for s in sigs)
    assert any(s.startswith("def baseline(") for s in sigs)
    assert any(s.startswith("def run_experiment(seed: int") for s in sigs)
    assert any(tf.path == CLAIM_TEST_FILE for tf in CANARY_DRAFT.public_tests)
    assert CANARY_DRAFT.seeds[:3] == [0, 1, 2] and CANARY_DRAFT.est_usd < 1.0


def test_fake_agent_responder_dispatches_by_schema():
    assert fake_agent_responder("scoper", "", "x", ScopeDraft) is CANARY_DRAFT
    assert fake_scoper_responder("scoper", "", "x", ScopeDraft) is CANARY_DRAFT
    assert isinstance(fake_agent_responder("scout_pass2", "", "x", Scorecard), Scorecard)
    assert isinstance(fake_agent_responder("scout_pass1", "", "ID: 2610.00001\n", EligibilityBatch), EligibilityBatch)


def test_factory_fake_model_answers_all_roles(tmp_path):
    from paper2code.llm.factory import make_chat_model
    from paper2code.manager.graph import RunContext

    model = make_chat_model(RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    assert model.parse("scoper", "", "x", ScopeDraft).value is CANARY_DRAFT
