from support_minimal_b import CaptureClient, MinimalBTests, body


class WriterInnerVoiceTests(MinimalBTests):
    def test_draft_can_voice_a_private_thought_without_an_explanation_quota(self):
        self.bible["story_design"] = {"total": self.book.chapters}
        self.outline["scenes"] = [{"characters": ["阿宁"], "action": "阿宁把湿钥匙递给门房", "result": "门房开了侧门"}]
        client = CaptureClient(self.out, [body(60)])
        self.run_chapter(client, state={"end_location": "南街茶院", "present": ["阿宁"]})

        prompt = next(call["prompt"] for call in client.calls if call["stage"] == "r2-ch2-draft")
        assert "每场戏至少两处" not in prompt
        assert "不写成心理陈述" not in prompt
        assert "没说出口的顾虑" in prompt
        assert "旁白不必再解释一遍" in prompt
        assert "阿宁把湿钥匙递给门房" in prompt and "门房开了侧门" in prompt
        assert "说话直接，先问再答。" in prompt
        assert "南街茶院" in prompt
