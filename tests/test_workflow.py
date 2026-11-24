from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import sys


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / ".agents/skills/smpl-papers/scripts"))
import run
import tex
import figures
import reviews
import sources
from records import write_json
from smoke_pdf import record_reviews


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "run"
        self.data = self.base / "results.csv"
        self.data.write_text("condition,score\nA,3\n", encoding="utf-8")

    def artifact(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def writing(self):
        manifest = run.initialize(self.root, "writing", "starter", "utf-8-sig", [self.data])
        write_json(self.root / "workplan.json", {"sections": [{"id": "results", "title": "Results", "queries": []}],
                                               "figures": []})
        return manifest

    def outline(self, manifest):
        self.artifact("outline.md", "# Outline")
        self.artifact("claims.json", '{"claims":[{"id":"C1","kind":"measured",'
                      '"statement":"Condition A scored 3.","evidence":[{"file":"results.csv",'
                      '"locator":"row 1, score"}]}]}')
        run.mark(self.root, manifest, "outline")

    def branches(self, manifest):
        self.outline(manifest)
        write_json(self.root / "citation-requests.json", {"sources": []})
        sources.discover(self.root)
        sources.verify(self.root)
        self.artifact("related-work.md", "No external claims in this fixture.")
        run.mark(self.root, manifest, "literature")
        figures.render(self.root)
        self.artifact("figure-review.json", '{"figures":[]}')
        run.mark(self.root, manifest, "figures")

    def draft(self, manifest):
        self.branches(manifest)
        self.artifact("title.tex", "Lookup example")
        self.artifact("body.tex", "Condition A scored 3.")
        run.mark(self.root, manifest, "draft")

    def reviewed_preview(self, manifest):
        self.draft(manifest)
        def fake_pages(pdf, folder):
            (folder / "page-1.png").write_bytes(b"fixture page")
            return ["page-1.png"]
        with patch.object(tex, "compile_pdf", return_value=b"%PDF-fixture"), patch.object(tex, "render_pages", fake_pages):
            candidate = tex.preview(self.root, None, self.base / "cache")
        manifest.update(run.load_json(self.root / "run.json"))
        record_reviews(self.root, candidate)
        return self.root / "reviews" / candidate["id"]

    def approve(self, manifest):
        self.reviewed_preview(manifest)
        reviews.decide(self.root)
        run.mark(self.root, manifest, "review")

    def test_encoding_preserves_text_and_handles_non_utf8(self):
        self.data.write_bytes("run,name\n1,caf\u00e9\n".encode("utf-8-sig"))
        self.writing()
        self.assertEqual((self.root / "materials/results.csv").read_bytes(),
                         "run,name\n1,caf\u00e9\n".encode())
        legacy = self.base / "legacy.txt"
        legacy.write_bytes(b"the result\x92s label")
        rejected = self.base / "rejected"
        with self.assertRaisesRegex(ValueError, "invalid utf-8"):
            run.initialize(rejected, "planning", "starter", "utf-8-sig", [legacy])
        self.assertFalse(rejected.exists())
        other = self.base / "legacy-run"
        run.initialize(other, "planning", "starter", "cp1252", [legacy])
        self.assertEqual((other / "materials/legacy.txt").read_text(encoding="utf-8"),
                         "the result\u2019s label")

    def test_plan_marks_only_after_both_artifacts(self):
        manifest = run.initialize(self.root, "planning", "starter", "utf-8-sig", [self.data])
        self.artifact("protocol.md", "# Plan\nCompare A and B in a matched experiment.")
        with self.assertRaisesRegex(ValueError, "evidence-checklist.md"):
            run.mark(self.root, manifest, "plan")
        self.artifact("evidence-checklist.md", "# Evidence\nRecord all raw measurements.")
        run.mark(self.root, manifest, "plan")
        self.assertTrue(run.current(self.root, manifest, "plan"))

    def test_measured_claim_requires_material_and_locator(self):
        manifest = self.writing()
        self.artifact("outline.md", "# Outline")
        self.artifact("claims.json", '{"claims":[{"id":"C1","kind":"measured",'
                      '"statement":"Claim","evidence":[{"file":"missing.csv",'
                      '"locator":"row 3"}]}]}')
        with self.assertRaisesRegex(ValueError, "missing.csv"):
            run.mark(self.root, manifest, "outline")
        self.assertFalse(run.current(self.root, manifest, "outline"))

    def test_unverified_citations_reject_draft(self):
        manifest = self.writing()
        self.branches(manifest)
        self.artifact("title.tex", "Lookup example")
        self.artifact("body.tex", r"A claimed fact \citep[Chapter 1]{unknown}.")
        with self.assertRaisesRegex(ValueError, "Unverified citation keys: unknown"):
            run.mark(self.root, manifest, "draft")
        self.assertFalse(run.current(self.root, manifest, "draft"))

    def test_resume_and_invalidate_changed_inputs(self):
        manifest = self.writing()
        self.approve(manifest)
        resumed = run.load_json(self.root / "run.json")
        self.assertFalse(run.refresh(self.root, resumed))
        self.assertTrue(run.current(self.root, resumed, "review"))
        self.artifact("body.tex", "Revised wording.")
        self.assertFalse(run.current(self.root, resumed, "review"))
        run.mark(self.root, resumed, "draft")
        self.assertTrue(run.current(self.root, resumed, "literature"))
        self.assertNotIn("review", resumed["stages"])
        self.data.write_text("condition,score\nA,4\n", encoding="utf-8")
        self.assertTrue(run.refresh(self.root, resumed))
        self.assertFalse(run.current(self.root, resumed, "outline"))
        self.assertEqual((self.root / "materials/results.csv").read_bytes(), self.data.read_bytes())

    def test_unresolved_major_review_blocks_build(self):
        manifest = self.writing()
        folder = self.reviewed_preview(manifest)
        path = folder / "evidence.json"
        report = run.load_json(path)
        report["findings"] = [{"severity": "major", "location": "Results", "issue": "Unsupported",
                               "resolution": "Supply data"}]
        write_json(path, report)
        self.assertEqual(reviews.decide(self.root)["decision"], "revise")
        with self.assertRaisesRegex(ValueError, "revise"):
            run.mark(self.root, manifest, "review")
        with self.assertRaisesRegex(ValueError, "current approved"):
            tex.build(self.root, None, self.base / "cache")

    def test_dispatch_uses_pending_branches_and_current_pdf(self):
        manifest = self.writing()
        self.outline(manifest)
        ready = run.next_work(self.root, manifest)
        self.assertEqual([r["role"] for r in ready["assignments"]], ["literature", "figures"])
        self.assertEqual(ready["helper"][-2:], ["prepare", str(self.root)])
        folder = self.reviewed_preview(manifest)
        layout = folder / "layout.json"
        report = run.load_json(layout)
        layout.unlink()
        self.assertEqual([r["role"] for r in run.next_work(self.root, manifest)["assignments"]], ["layout"])
        write_json(layout, {**report, "pages": []})
        with self.assertRaisesRegex(ValueError, "every rendered page"):
            reviews.decide(self.root)
        write_json(layout, {**report, "pdf_sha256": "other-version"})
        with self.assertRaisesRegex(ValueError, "another PDF"):
            reviews.decide(self.root)
        write_json(layout, report)
        reviews.decide(self.root)
        run.mark(self.root, manifest, "review")
        self.assertEqual(run.next_work(self.root, manifest)["stage"], "build")

    def test_failed_rebuild_does_not_present_stale_pdf_as_current(self):
        manifest = self.writing()
        self.approve(manifest)
        self.artifact("paper.pdf", "previous PDF")
        with patch.object(tex.shutil, "copytree", side_effect=OSError("Publication failed")):
            with self.assertRaisesRegex(OSError, "Publication failed"):
                tex.build(self.root, None, self.base / "cache")
        updated = run.load_json(self.root / "run.json")
        self.assertEqual(updated["stages"]["build"]["status"], "failed")
        self.assertFalse(run.current(self.root, updated, "build"))
        self.assertFalse((self.root / "paper.pdf").exists())
        self.assertTrue((self.root / "paper.previous.pdf").exists())
        self.assertIn("Publication failed", (self.root / "build.log").read_text(encoding="utf-8"))

    def test_bad_author_kit_checksum_never_extracts(self):
        cache = self.base / "cache"
        destination = self.base / "destination"
        cache.mkdir()
        destination.mkdir()
        (cache / "cvpr2025.zip").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "checksum"):
            tex.prepare_template("cvpr2025", destination, cache)
        self.assertFalse((destination / "cvpr.sty").exists())


if __name__ == "__main__":
    unittest.main()
