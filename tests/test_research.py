"""Focused tests for live evidence, figure handoffs, routing and measured trials."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import pymupdf

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / ".agents/skills/smpl-papers/scripts"))
import experiments
import figures
import roles
import run
import sources
from records import json_hash, load_json, write_json


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "run"
        self.input = self.base / "measurements.csv"
        self.input.write_text("group,ms\nA,10\nB,8\n")
        self.plan = {"sections": [{"id": "methods", "title": "Methods", "queries": ["paper search"]}], "figures": []}
        self.record = {"key": "paper", "section": "methods", "title": "A measured result",
                       "url": "https://primary.example/paper", "quote": "The measurement was eight.",
                       "locator": "Results", "supports": "The study reports eight units.", "doi": "10.1234/abc"}

    def writing(self):
        manifest = run.initialize(self.root, "writing", "starter", "utf-8-sig", [self.input])
        write_json(self.root / "workplan.json", self.plan)
        return manifest

    def responses(self, url, accept="*/*", headers=None, body=None):
        if url == self.record["url"]:
            return b"<title>A measured result</title><p>The measurement was eight.</p>", url, "text/html"
        if "/transform/" in url:
            return b"@article{publisherKey, title={A measured result}}", url, "application/x-bibtex"
        payload = {"message": {"DOI": "10.1234/abc", "title": ["A measured result"],
                               "published": {"date-parts": [[2020, 3, 4]]}}}
        return json.dumps(payload).encode(), url, "application/json"

    def test_verified_citation_requires_retrieval_and_bound_passage(self):
        self.writing()
        requests = {"sources": [self.record]}
        write_json(self.root / "citation-requests.json", requests)
        with patch.object(sources, "fetch", side_effect=self.responses) as network:
            sources.verify(self.root)
        self.assertEqual(network.call_count, 3)
        self.assertEqual(run.literature_keys(self.root), {"paper"})
        self.assertIn("@article{paper,", (self.root / "references.bib").read_text())
        data = load_json(self.root / "literature.json")
        data["sources"][0]["supports"] = "A different claim"
        write_json(self.root / "literature.json", data)
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            run.literature_keys(self.root)
        self.record["quote"] = "A sentence that does not occur."
        write_json(self.root / "citation-requests.json", requests)
        with patch.object(sources, "fetch", side_effect=self.responses), self.assertRaisesRegex(ValueError, "passage"):
            sources.verify(self.root)
        with self.assertRaises(ValueError):
            run.literature_keys(self.root)

    def test_live_lookup_failure_and_cutoff_prevent_verification(self):
        self.plan["cutoff"] = "2019-12-31"
        self.writing()
        write_json(self.root / "citation-requests.json", {"sources": [self.record]})
        with patch.object(sources, "fetch", side_effect=OSError("unreachable")), self.assertRaises(OSError):
            sources.verify(self.root)
        with patch.object(sources, "fetch", side_effect=self.responses), self.assertRaisesRegex(ValueError, "cutoff"):
            sources.verify(self.root)
        self.assertFalse((self.root / "literature.json").exists())

    def test_self_reported_verification_cannot_replace_a_receipt(self):
        self.writing()
        forged = {**self.record, "url": "https://example.invalid/paper"}
        write_json(self.root / "citation-requests.json", {"sources": [forged]})
        write_json(self.root / "literature.json", {"request_hash": json_hash([forged]),
                   "sources": [{**forged, "verified": True}]})
        with self.assertRaisesRegex(ValueError, "verified flag"):
            run.literature_keys(self.root)

    def test_fixed_pdf_checks_the_cited_page_without_network(self):
        source = self.base / "paper.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "A measured result")
            pdf.new_page().insert_text((72, 72), "The measurement was eight.")
            pdf.save(source)
        run.initialize(self.root, "writing", "starter", "utf-8-sig", [source])
        write_json(self.root / "workplan.json", {**self.plan, "citation_mode": "fixture"})
        request = {**self.record, "fixture":"materials/paper.pdf", "locator":"p. 1"}
        write_json(self.root / "citation-requests.json", {"sources":[request]})
        with patch.object(sources, "fetch", side_effect=AssertionError("Unexpected live lookup")):
            with self.assertRaisesRegex(ValueError, "passage"):
                sources.verify(self.root)
            request["locator"] = "p. 2"
            write_json(self.root / "citation-requests.json", {"sources":[request]})
            sources.verify(self.root)
        _, linked = sources.verified_sources(self.root)
        self.assertEqual(load_json(self.root / linked[-1])["page"], 2)

    def test_provider_fallback_and_complete_outage(self):
        self.plan["cutoff"] = "2020-12-31"
        self.writing()
        paper = {"doi": "10.1/a", "title": "Test", "date": "2019-01-01", "url": "https://example.org"}
        with patch.object(sources, "query_index", side_effect=[[paper, {**paper, "date": "2021-01-01"}],
                                                               OSError("rate limited")]):
            sources.discover(self.root)
        search = load_json(self.root / "discovery.json")["searches"][0]
        self.assertEqual([p["doi"] for p in search["candidates"]], ["10.1/a"])
        self.assertEqual(search["completed"], ["crossref"])
        self.assertIn("semantic-scholar", search["errors"])
        with patch.object(sources, "query_index", side_effect=OSError("outage")), self.assertRaisesRegex(ValueError, "failed"):
            sources.discover(self.root)
        self.assertEqual(load_json(self.root / "discovery.json")["searches"][0]["completed"], [])

    def test_google_search_uses_grounding_and_registry(self):
        def gateway(url, accept="*/*", headers=None, body=None):
            if body:
                self.assertIn("google_search", json.loads(body)["tools"][0])
                self.assertIn("x-goog-api-key", headers)
                payload = {"candidates": [{"groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://a.example/p"}}]},
                           "content": {"parts": [{"text": '["A measured result"]'}]}}]}
            else:
                payload = {"message": {"items": [{"DOI": "10.1234/abc", "title": ["A measured result"],
                           "published": {"date-parts": [[2020]]}, "URL": "https://doi.org/10.1234/abc"}]}}
            return json.dumps(payload).encode(), url, "application/json"
        with patch.dict(sources.os.environ, {"GEMINI_API_KEY": "fixture", "SMPL_SEARCH_MODEL": "fixture"}), \
                patch.object(sources, "fetch", side_effect=gateway):
            result = sources.query_index("google", "test search", None, 2)
        self.assertEqual(result[0]["doi"], "10.1234/abc")
        self.assertEqual(result[0]["grounding_links"], ["https://a.example/p"])

    def test_diagram_review_is_bound_to_the_rendered_image(self):
        self.plan["figures"] = [{"id":"flow", "section":"methods", "kind":"diagram", "source":"materials/measurements.csv",
                                "caption":"Illustrative relationship.", "nodes":["Input","Result"], "edges":[["Input","Result"]]}]
        self.writing()
        figures.render(self.root)
        fig = load_json(self.root / "figures.json")["figures"][0]
        critique = {"id": "flow", "sha256": fig["sha256"], "decision": "revise", "assessment": "Inspect edge labels."}
        write_json(self.root / "figure-review.json", {"figures": [critique]})
        with self.assertRaisesRegex(ValueError, "inspect"):
            figures.checked_figures(self.root)
        self.plan["figures"][0]["nodes"][1] = "Measured result"
        self.plan["figures"][0]["edges"] = [["Input", "Measured result"]]
        write_json(self.root / "workplan.json", self.plan)
        figures.render(self.root)
        self.assertTrue((self.root / "figure-history" / "flow" / fig["sha256"] / "critique.json").is_file())
        critique["decision"] = "approve"
        write_json(self.root / "figure-review.json", {"figures": [critique]})
        with self.assertRaisesRegex(ValueError, "inspect"):
            figures.checked_figures(self.root)
        critique["sha256"] = run.file_hash(self.root / fig["path"])
        write_json(self.root / "figure-review.json", {"figures": [critique]})
        self.assertIn(fig["path"], figures.checked_figures(self.root))

    def test_specialist_is_resolved_by_registered_name(self):
        path = self.base / "skills" / "different-folder" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: evaluating-llms-harness\ndescription: Fixture\n---\n")
        brief = roles.brief(self.root, "experiment", [self.base / "skills"], "evaluation")
        self.assertIn(str(path.resolve()), brief)
        self.assertIn("Register", brief)

    def test_pending_illustration_hands_off_to_the_figure_role(self):
        self.plan["figures"] = [{"id":"concept", "kind":"illustration", "section":"methods",
                                "source":"materials/measurements.csv", "caption":"Illustrative concept.",
                                "file":"figures/concept.png", "prompt":"figures/concept.md"}]
        self.writing()
        figures.render(self.root, defer_custom=True)
        self.assertEqual(load_json(self.root / "figures.json")["pending"], ["concept"])
        (self.root / "figures/concept.png").write_bytes(b"fixture graphic")
        (self.root / "figures/concept.md").write_text("Draw the conceptual relation in the measurements.")
        figures.render(self.root)
        item = load_json(self.root / "figures.json")["figures"][0]
        write_json(self.root / "figure-review.json", {"figures": [{"id":"concept","sha256":item["sha256"],
                   "assessment":"Fixture visual inspection.","decision":"approve"}]})
        self.assertIn(item["prompt"], figures.checked_figures(self.root))

    def test_registered_trial_records_raw_metrics_and_resumes(self):
        runner = self.base / "measure.py"
        runner.write_text("import os, json\nfrom pathlib import Path\n"
                          "Path(os.environ['SMPL_METRICS']).write_text(json.dumps({'ms': 8}))\n")
        manifest = run.initialize(self.root, "experiment", "starter", "utf-8-sig", [runner])
        (self.root / "protocol.md").write_text("Latency below 9 confirms the prediction.")
        (self.root / "evidence-checklist.md").write_text("Save the measured latency.")
        run.mark(self.root, manifest, "plan")
        spec = {"id":"trial", "hypothesis":"Latency is low", "metric":"ms", "favorable":9,
                "unfavorable":11, "command":["python","materials/measure.py"]}
        folder = experiments.register(self.root, spec)
        result = experiments.execute(self.root, "trial")
        self.assertEqual((result["value"], result["outcome"]), (8, "supported"))
        self.assertEqual(experiments.execute(self.root, "trial"), result)
        self.assertEqual(len(list(folder.glob("attempt-*"))), 1)
        with self.assertRaisesRegex(ValueError, "different registration"):
            experiments.register(self.root, {**spec, "favorable":7})
        self.assertEqual(experiments.outcome(spec, 10), "inconclusive")
        self.assertEqual(experiments.outcome(spec, 12), "not-supported")
        write_json(self.root / "experiments.json", experiments.summary(self.root)[0])
        (self.root / "findings.md").write_text("The measured latency was 8 ms.")
        run.mark(self.root, manifest, "experiment")
        self.assertTrue(run.current(self.root, manifest, "experiment"))
        write_json(self.root / result["metrics"], {"ms": 12})
        self.assertFalse(run.current(self.root, manifest, "experiment"))
        with self.assertRaisesRegex(ValueError, "differs"):
            experiments.execute(self.root, "trial")

    def test_earlier_writing_approval_requires_new_evidence(self):
        manifest = self.writing()
        manifest["stages"]["review"] = {"status":"complete", "fingerprint":{}}
        self.assertFalse(run.current(self.root, manifest, "review"))


if __name__ == "__main__":
    unittest.main()
