"""Both AI routes must use the same formal customer address."""

from reply.customer_address import address_instruction, normalize_address
from reply.claude_client import _build_system_prompt
from wecom.ai_panel import build_ask_prompt, _extract_final_reply


def test_address_instruction_uses_surname_and_known_gender():
    assert "周先生" in address_instruction("周斌(男)-1234")
    assert "李女士" in address_instruction("李婷(女)-1234")
    assert "不要猜测性别" in address_instruction("周斌-1234")


def test_both_ai_prompts_override_historical_informal_examples():
    own = _build_system_prompt([{"q": "你好", "a": "好嘞斌哥"}], "周斌(男)-1234")
    native = build_ask_prompt("周斌(男)-1234")
    assert "周先生" in own and "周先生" in native
    assert "不要照搬" in own and "不要照搬" in native
    assert "更倾向「X哥」" not in own
    assert "周先生" in _build_system_prompt(None, "周斌(男)-1234")


def test_normalize_male_and_female_and_generic_vocatives():
    assert normalize_address("好的斌哥，马上看。", "周斌(男)-1234") == "好的周先生，马上看。"
    assert normalize_address("李姐您好，小姐姐稍等。", "李婷(女)-12") == "李女士您好，李女士稍等。"
    assert normalize_address("老哥，稍等哈。", "周斌(男)-1") == "周先生，稍等哈。"


def test_unknown_gender_and_english_name_do_not_guess():
    assert normalize_address("好的斌哥。", "周斌-1234") == "好的。"
    assert "不要猜测性别" in address_instruction("Alex-1234")
    assert normalize_address("Alex哥，您好", "Alex-1234") == "Alex，您好"


def test_do_not_change_relatives_or_other_people():
    assert normalize_address("您姐姐和王先生一起出行吗？", "周斌(男)-1234") == "您姐姐和王先生一起出行吗？"


def test_native_reply_format_still_extracts_clean_body():
    assert _extract_final_reply("===回复正文开始===\n周先生您好\n===回复正文结束===") == "周先生您好"
