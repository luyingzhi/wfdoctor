# -*- coding: utf-8 -*-
"""wfdoctor 最小回归测试（不需要真实 ComfyUI：用 fake object_info）"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import wfdoctor  # noqa: E402

SAMPLES = os.path.join(HERE, "samples")

# 伪造一个 object_info：只包含几个核心节点
FAKE_OBJECT_INFO = {
    "KSampler": {"input": {"required": {
        "model": ["MODEL"],
        "seed": ["INT", {"default": 0}],
        "steps": ["INT", {"default": 20}],
        "cfg": ["FLOAT", {"default": 8.0}],
        "sampler_name": [["euler", "dpmpp_2m"]],
        "scheduler": [["normal", "karras"]],
        "denoise": ["FLOAT", {"default": 1.0}],
    }}, "python_module": "nodes"},
    "UNETLoader": {"input": {"required": {
        "unet_name": [["Krea2\\krea2_turbo_fp8_scaled.safetensors"]],
        "weight_dtype": [["default", "fp8_e4m3fn"]],
    }}, "python_module": "nodes"},
    "VAELoader": {"input": {"required": {
        "vae_name": [["krea2RealVae_v10.safetensors"]],
    }}, "python_module": "nodes"},
    "CLIPLoader": {"input": {"required": {
        "clip_name": [["qwen3vl_4b_fp8_scaled.safetensors"]],
        "type": [["krea2"]],
        "device": [["default"]],
    }}, "python_module": "nodes"},
    "LoraLoaderModelOnly": {"input": {"required": {
        "model": ["MODEL"],
        "lora_name": [["Krea2\\krea2_identity_edit_v1_2-人物图像编辑.safetensors"]],
        "strength_model": ["FLOAT", {"default": 1.0}],
    }}, "python_module": "nodes"},
    "VAEDecode": {"input": {"required": {
        "samples": ["LATENT"], "vae": ["VAE"],
    }}, "python_module": "nodes"},
    "SaveImage": {"input": {"required": {
        "images": ["IMAGE"],
        "filename_prefix": ["STRING", {"default": "ComfyUI"}],
    }}, "python_module": "nodes"},
    "EmptySD3LatentImage": {"input": {"required": {
        "width": ["INT", {"default": 1024}],
        "height": ["INT", {"default": 1024}],
        "batch_size": ["INT", {"default": 1}],
    }}, "python_module": "nodes"},
    "CLIPTextEncode": {"input": {"required": {
        "clip": ["CLIP"],
        "text": ["STRING", {"default": "", "multiline": True}],
    }}, "python_module": "nodes"},
}


class TestFormats(unittest.TestCase):
    def test_detect_ui(self):
        self.assertEqual(wfdoctor.detect_format({"nodes": []}), "ui")

    def test_detect_api(self):
        self.assertEqual(
            wfdoctor.detect_format({"1": {"class_type": "KSampler", "inputs": {}}}), "api")

    def test_detect_api_wrapped(self):
        self.assertEqual(
            wfdoctor.detect_format({"prompt": {"1": {"class_type": "KSampler"}}}), "api")

    def test_detect_unknown(self):
        self.assertEqual(wfdoctor.detect_format({"hello": "world"}), "unknown")


class TestFrontendWhitelist(unittest.TestCase):
    def test_reroute_not_flagged(self):
        r = wfdoctor.Report()
        wf = {"nodes": [{"id": 1, "type": "Reroute", "inputs": [], "widgets_values": []},
                        {"id": 2, "type": "SetNode", "inputs": [], "widgets_values": []},
                        {"id": 3, "type": "GetNode", "inputs": [], "widgets_values": []}]}
        tmp = os.path.join(HERE, "_tmp_fe.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(wf, fh)
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)
            self.assertEqual(r.errors, [], [f.__dict__ for f in r.findings])
        finally:
            os.remove(tmp)


class TestMissingNode(unittest.TestCase):
    def test_missing_node_detected(self):
        r = wfdoctor.Report()
        wf = {"nodes": [{"id": 7, "type": "WanVideoEncode", "inputs": [], "widgets_values": []}]}
        tmp = os.path.join(HERE, "_tmp_missing.json")
        open(tmp, "w", encoding="utf-8").write(json.dumps(wf, ensure_ascii=False))
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)

            self.assertEqual(len(r.errors), 1)
            self.assertEqual(r.errors[0].kind, "missing_node")
            self.assertEqual(r.errors[0].node_type, "WanVideoEncode")
        finally:
            os.remove(tmp)


class TestMissingModel(unittest.TestCase):
    def test_missing_model_detected(self):
        r = wfdoctor.Report()
        wf = {"nodes": [{
            "id": 3, "type": "UNETLoader",
            "inputs": [], "widgets_values": ["no_such_model.safetensors", "default"],
        }]}
        tmp = os.path.join(HERE, "_tmp_model.json")
        open(tmp, "w", encoding="utf-8").write(json.dumps(wf, ensure_ascii=False))
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)
            kinds = [f.kind for f in r.findings]
            self.assertIn("missing_model", kinds)
        finally:
            os.remove(tmp)

    def test_existing_model_ok(self):
        r = wfdoctor.Report()
        wf = {"nodes": [{
            "id": 3, "type": "UNETLoader",
            "inputs": [], "widgets_values": ["Krea2\\krea2_turbo_fp8_scaled.safetensors", "default"],
        }]}
        tmp = os.path.join(HERE, "_tmp_ok.json")
        open(tmp, "w", encoding="utf-8").write(json.dumps(wf, ensure_ascii=False))
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)
            self.assertTrue(r.ok(), [f.__dict__ for f in r.findings])
        finally:
            os.remove(tmp)


class TestApiFormat(unittest.TestCase):
    def test_api_format_missing_model(self):
        r = wfdoctor.Report()
        wf = {"1": {"class_type": "UNETLoader",
                    "inputs": {"unet_name": "ghost.safetensors", "weight_dtype": "default"}}}
        tmp = os.path.join(HERE, "_tmp_api.json")
        open(tmp, "w", encoding="utf-8").write(json.dumps(wf, ensure_ascii=False))
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)
            self.assertEqual(r.format, "api")
            self.assertIn("missing_model", [f.kind for f in r.findings])
        finally:
            os.remove(tmp)


class TestSubgraph(unittest.TestCase):
    def test_subgraph_nodes_are_checked(self):
        r = wfdoctor.Report()
        wf = {
            "nodes": [{"id": 1, "type": "SG-ID", "inputs": [], "widgets_values": []}],
            "definitions": {"subgraphs": [{
                "id": "SG-ID", "name": "inner",
                "nodes": [{"id": 9, "type": "GhostInnerNode", "inputs": [], "widgets_values": []}],
            }]},
        }
        tmp = os.path.join(HERE, "_tmp_sub.json")
        open(tmp, "w", encoding="utf-8").write(json.dumps(wf, ensure_ascii=False))
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)
            types = [f.node_type for f in r.errors]
            self.assertIn("GhostInnerNode", types)
            # 子图容器自身的虚拟 id 不应该被当成缺节点
            self.assertNotIn("SG-ID", types)
        finally:
            os.remove(tmp)


class TestBrokenJson(unittest.TestCase):
    def test_broken_json(self):
        r = wfdoctor.Report()
        tmp = os.path.join(HERE, "_tmp_broken.json")
        open(tmp, "w", encoding="utf-8").close()
        try:
            wfdoctor.check_workflow(tmp, FAKE_OBJECT_INFO, r)
            self.assertFalse(r.ok())
            self.assertEqual(r.errors[0].kind, "broken_link")
        finally:
            os.remove(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
