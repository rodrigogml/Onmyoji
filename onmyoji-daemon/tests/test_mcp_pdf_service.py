"""Contratos observáveis de PDFs reais e publicação transacional de saídas."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

fitz = pytest.importorskip("pymupdf")

from onmyoji_daemon.mcp.document_processing.contracts import DocumentError
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.pdf.service import execute, pages


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "source.pdf"
    with fitz.open() as doc:
        for text in ("Alpha confidential", "Beta action", "Gamma final"):
            page = doc.new_page(width=300, height=400)
            page.insert_text((36, 60), text)
        doc.set_metadata({"title": "Original"})
        doc.save(path)
    return path


def context(tmp_path):
    return DocumentContext(tmp_path / "instance", "pdf", "test", {"readable_roots": [str(tmp_path)], "writable_roots": [str(tmp_path / "output")], "work_directory": str(tmp_path / "work")}, {"max_pages": 2000, "max_pixels": 50000000}, tmp_path / "work" / "job")


def run(tmp_path, document, operation, **arguments):
    ctx = context(tmp_path)
    output = tmp_path / "output" / "result.pdf"
    args = {"input_path": str(document), "output_path": str(output), **arguments}
    result = execute(operation, args, ctx)
    ctx.publish()
    return result, output


@pytest.mark.parametrize("value,expected", [(None, [0, 1, 2]), ("3,1-2,1", [2, 0, 1, 0]), ([3, 1], [2, 0])])
def test_page_selection_preserves_order_and_duplicates(value, expected):
    assert pages(value, 3) == expected


@pytest.mark.parametrize("value", ["0", "4", "2-1", "1-50000", "a", "1,", [True], [-1], [], 1])
def test_page_selection_rejects_invalid_or_unbounded_ranges(value):
    with pytest.raises(DocumentError):
        pages(value, 3)


def test_inspection_and_text_extraction_preserve_source(tmp_path, document):
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    ctx = context(tmp_path)
    inspected = execute("inspect", {"input_path": str(document)}, ctx)
    extracted = execute("extract_text", {"input_path": str(document), "pages": "3,1"}, ctx)
    assert inspected["pages"] == 3
    assert inspected["metadata"]["title"] == "Original"
    assert [block["page"] for block in extracted["blocks"]] == [3, 1]
    assert all(block["source"] == "native" for block in extracted["blocks"])
    assert hashlib.sha256(document.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_regions_are_in_the_visible_rotated_coordinate_space(tmp_path, document, rotation):
    rotated = tmp_path / "rotated.pdf"
    with fitz.open(document) as doc:
        doc[0].set_rotation(rotation); doc.save(rotated)
    ctx = context(tmp_path)
    search = execute("search", {"input_path": str(rotated), "query": "Alpha", "pages": "1"}, ctx)
    box = search["matches"][0]["bbox"]
    extracted = execute("extract_text", {"input_path": str(rotated), "pages": "1", "region": box}, ctx)
    assert "Alpha" in extracted["text"]


def test_region_uses_cropbox_and_rotation_together(tmp_path, document):
    path = tmp_path / "cropped.pdf"
    with fitz.open(document) as doc:
        doc[0].set_cropbox(fitz.Rect(20, 20, 280, 380)); doc[0].set_rotation(90); doc.save(path)
    ctx = context(tmp_path)
    found = execute("search", {"input_path": str(path), "query": "Alpha", "pages": "1"}, ctx)["matches"][0]
    result = execute("extract_text", {"input_path": str(path), "region": found["bbox"], "pages": "1"}, ctx)
    assert "Alpha" in result["text"]


def test_regex_search_reports_actual_match_coordinates(tmp_path, document):
    result = execute("search", {"input_path": str(document), "query": "A[a-z]+", "regex": True}, context(tmp_path))
    assert result["matches"][0]["text"] == "Alpha"
    assert result["matches"][0]["page"] == 1


def test_extract_reorder_delete_duplicate_and_insert_pages(tmp_path, document):
    for action, options, expected in [("extract", {"pages": "3,1"}, ["Gamma", "Alpha"]), ("delete", {"pages": "2"}, ["Alpha", "Gamma"]), ("duplicate", {"pages": "2"}, ["Alpha", "Beta", "Beta", "Gamma"]), ("insert", {"pages": "1", "insert_path": str(document), "insert_at": 1}, ["Alpha", "Beta", "Gamma", "Alpha"])]:
        ctx = context(tmp_path)
        out = tmp_path / "output" / f"{action}.pdf"
        execute("select_pages", {"input_path": str(document), "output_path": str(out), "action": action, **options}, ctx)
        assert not out.exists()
        ctx.publish()
        with fitz.open(out) as doc:
            assert [page.get_text().split()[0] for page in doc] == expected


def test_merge_interleave_and_split_ranges(tmp_path, document):
    result, output = run(tmp_path, document, "merge", input_paths=[str(document), str(document)], action="interleave")
    assert result["pages"] == 6
    with fitz.open(output) as doc:
        assert [page.get_text().split()[0] for page in doc] == ["Alpha", "Alpha", "Beta", "Beta", "Gamma", "Gamma"]
    ctx = context(tmp_path)
    execute("split", {"input_path": str(document), "ranges": ["1-2", "3"], "output_directory": str(tmp_path / "output" / "split")}, ctx)
    ctx.publish()
    with fitz.open(tmp_path / "output" / "split" / "part-0001.pdf") as doc:
        assert len(doc) == 2


@pytest.mark.parametrize("action,extra", [("rotate", {"angle": 90}), ("crop", {"region": [20, 20, 280, 380]}), ("resize", {"width": 600, "height": 800}), ("margins", {"margins": [10, 20, 30, 40]})])
def test_page_transformations_produce_reopenable_documents(tmp_path, document, action, extra):
    _, output = run(tmp_path, document, "transform_pages", action=action, pages="1", **extra)
    with fitz.open(output) as doc:
        assert len(doc) == 3
        assert "Alpha" in doc[0].get_text()
        if action == "rotate": assert doc[0].rotation == 90
        if action == "resize": assert doc[0].rect.width == 600


def test_render_pages_and_contact_sheet(tmp_path, document):
    ctx = context(tmp_path)
    result = execute("render_pages", {"input_path": str(document), "pages": "1,2", "dpi": 72, "output_directory": str(tmp_path / "output")}, ctx)
    ctx.publish()
    assert len(result["images"]) == 2
    assert (tmp_path / "output" / "page-0001.png").is_file()
    ctx = context(tmp_path)
    execute("render_pages", {"input_path": str(document), "contact_sheet": True, "output_path": str(tmp_path / "output" / "sheet.png")}, ctx)
    ctx.publish()
    assert (tmp_path / "output" / "sheet.png").is_file()


def test_rotated_page_render_keeps_visible_dimensions(tmp_path, document):
    path = tmp_path / "rotated.pdf"
    with fitz.open(document) as doc:
        doc[0].set_rotation(90); doc.save(path)
    ctx = context(tmp_path)
    result = execute("render_pages", {"input_path": str(path), "pages": "1", "dpi": 72, "output_path": str(tmp_path / "output" / "page.png")}, ctx)
    assert result["images"][0]["width"] == 400
    assert result["images"][0]["height"] == 300


def test_pixel_budget_is_checked_before_rendering(tmp_path, document):
    ctx = context(tmp_path); ctx.defaults["max_pixels"] = 100
    with pytest.raises(DocumentError) as error:
        execute("render_pages", {"input_path": str(document), "output_directory": str(tmp_path / "output")}, ctx)
    assert error.value.code == "render_limit"


def test_metadata_bookmarks_and_annotations_roundtrip(tmp_path, document):
    result, output = run(tmp_path, document, "metadata", action="set", values={"title": "Updated"})
    assert result["pages"] == 3
    with fitz.open(output) as doc:
        assert doc.metadata["title"] == "Updated"
    ctx = context(tmp_path); bookmarks = tmp_path / "output" / "bookmarks.pdf"
    execute("bookmarks", {"input_path": str(document), "output_path": str(bookmarks), "action": "set", "entries": [[1, "First", 1], [1, "Third", 3]]}, ctx); ctx.publish()
    assert execute("bookmarks", {"input_path": str(bookmarks)}, context(tmp_path))["entries"] == [[1, "First", 1], [1, "Third", 3]]
    ctx = context(tmp_path); annotations = tmp_path / "output" / "annotations.pdf"
    execute("annotations", {"input_path": str(document), "output_path": str(annotations), "action": "add", "entries": [{"page": 1, "rect": [30, 40, 180, 70], "type": "highlight", "text": "Review"}]}, ctx); ctx.publish()
    result = execute("annotations", {"input_path": str(annotations)}, context(tmp_path))
    assert result["annotations"][0]["info"]["content"] == "Review"


def test_attachments_extract_to_explicit_destination_not_embedded_filename(tmp_path, document):
    content = tmp_path / "note.txt"; content.write_text("attached", encoding="utf-8")
    _, attached = run(tmp_path, document, "attachments", action="add", attachment_name="../../unsafe.txt", attachment_path=str(content))
    ctx = context(tmp_path)
    result = execute("attachments", {"input_path": str(attached), "action": "extract", "output_directory": str(tmp_path / "output" / "attachments")}, ctx)
    ctx.publish()
    assert result["attachments"][0]["name"] == "../../unsafe.txt"
    assert (tmp_path / "output" / "attachments" / "attachment-0001.bin").read_bytes() == b"attached"


def test_redaction_removes_text_and_old_revisions_from_serialized_result(tmp_path, document):
    result, output = run(tmp_path, document, "apply_redactions", query="confidential")
    assert result["applied"] is True
    with fitz.open(output) as doc:
        assert "confidential" not in doc[0].get_text()
        assert "Alpha" in doc[0].get_text()
    assert b"confidential" not in output.read_bytes()
    assert b"confidential" in document.read_bytes() or "confidential" in fitz.open(document)[0].get_text()


def test_preview_keeps_original_content_and_marks_it_as_unapplied(tmp_path, document):
    result, output = run(tmp_path, document, "preview_redactions", query="confidential")
    assert result["applied"] is False
    with fitz.open(output) as doc:
        assert "confidential" in doc[0].get_text()


def test_sanitize_removes_embedded_files_and_metadata(tmp_path, document):
    result, output = run(tmp_path, document, "sanitize")
    with fitz.open(output) as doc:
        assert not doc.embfile_names()
        assert doc.metadata["title"] == ""


def test_sanitize_removes_catalog_and_page_active_actions(tmp_path, document):
    path = tmp_path / "actions.pdf"
    with fitz.open(document) as doc:
        action = doc.get_new_xref(); doc.update_object(action, "<< /Type /Action /S /Launch /F (not-a-real-program.exe) >>")
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{action} 0 R")
        doc.xref_set_key(doc[0].xref, "AA", f"<< /O {action} 0 R >>")
        doc.save(path)
    _, output = run(tmp_path, path, "sanitize")
    with fitz.open(output) as doc:
        assert doc.xref_get_key(doc.pdf_catalog(), "OpenAction")[0] == "null"
        assert doc.xref_get_key(doc[0].xref, "AA")[0] == "null"
        assert b"not-a-real-program.exe" not in output.read_bytes()


def test_redaction_removes_image_pixels_vectors_and_embedded_copies(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "sensitive.pdf"; image = tmp_path / "sensitive.png"
    Image.new("RGB", (100, 100), "red").save(image)
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_image(fitz.Rect(40, 100, 140, 200), filename=str(image))
        page.draw_rect(fitz.Rect(50, 110, 130, 190), color=(1, 0, 0), fill=(1, 0, 0))
        page.insert_text((55, 150), "secret", render_mode=3)
        doc.embfile_add("copy.png", image.read_bytes())
        doc.save(source)
    _, output = run(tmp_path, source, "apply_redactions", redactions=[{"page": 1, "rect": [30, 90, 150, 210]}])
    with fitz.open(output) as doc:
        assert not doc.embfile_names()
        assert "secret" not in doc[0].get_text()
        assert not any(shape["fill"] == (1, 0, 0) for shape in doc[0].get_drawings())
        assert all(doc.extract_image(item[0])["image"] != image.read_bytes() for item in doc[0].get_images())


def test_compare_reports_page_and_visual_changes(tmp_path, document):
    modified = tmp_path / "changed.pdf"
    with fitz.open(document) as doc:
        doc[0].insert_text((36, 100), "Added"); doc.save(modified)
    result = execute("compare", {"input_path": str(document), "other_path": str(modified), "output_directory": str(tmp_path / "output")}, context(tmp_path))
    assert not result["equal"]
    assert result["differences"][0]["page"] == 1
    assert result["differences"][0]["text_changed"]


def test_protection_uses_resolved_password_and_can_be_decrypted(tmp_path, document):
    ctx = context(tmp_path); ctx.resolve_password = lambda ref: "secret-password"
    output = tmp_path / "output" / "encrypted.pdf"
    execute("protect", {"input_path": str(document), "output_path": str(output), "new_password_ref": {"credential_profile": "vault", "credential_entry_path": "pdf"}}, ctx); ctx.publish()
    with fitz.open(output) as doc:
        assert doc.needs_pass
        assert doc.authenticate("secret-password")
    ctx = context(tmp_path); ctx.resolve_password = lambda ref: "secret-password"
    destination = tmp_path / "output" / "decrypted.pdf"
    execute("protect", {"input_path": str(output), "output_path": str(destination), "action": "decrypt", "password_ref": {"credential_profile": "vault", "credential_entry_path": "pdf"}}, ctx); ctx.publish()
    with fitz.open(destination) as doc:
        assert not doc.needs_pass
        assert "Alpha" in doc[0].get_text()


def test_transform_and_merge_preserve_input_encryption(tmp_path, document):
    encrypted = tmp_path / "encrypted.pdf"
    with fitz.open(document) as doc:
        doc.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="test", owner_pw="test")
    ref = {"credential_profile": "vault", "credential_entry_path": "pdf"}
    for operation, extra in (("transform_pages", {"action": "rotate", "angle": 90}), ("merge", {"input_paths": [str(encrypted), str(document)]})):
        ctx = context(tmp_path); ctx.resolve_password = lambda reference: "test"
        output = tmp_path / "output" / (operation + ".pdf")
        execute(operation, {"input_path": str(encrypted), "password_ref": ref, "output_path": str(output), **extra}, ctx); ctx.publish()
        with fitz.open(output) as doc:
            assert doc.needs_pass
            assert doc.authenticate("test")
            assert "Alpha" in doc[0].get_text()


def test_forms_edit_preserves_encryption(tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    form = tmp_path / "form.pdf"
    out = canvas.Canvas(str(form)); out.acroForm.textfield(name="name", x=40, y=700, width=200, height=30); out.showPage(); out.save()
    encrypted = tmp_path / "encrypted.pdf"
    with fitz.open(form) as doc:
        doc.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="test", owner_pw="test")
    ctx = context(tmp_path); ctx.resolve_password = lambda reference: "test"
    output = tmp_path / "output" / "filled.pdf"
    execute("forms", {"input_path": str(encrypted), "output_path": str(output), "action": "fill", "values": {"name": "Rodrigo"}, "password_ref": {"credential_profile": "vault", "credential_entry_path": "pdf"}}, ctx); ctx.publish()
    with fitz.open(output) as doc:
        assert doc.needs_pass
        assert doc.authenticate("test")
        assert "Rodrigo" in doc[0].get_text()


def test_form_fill_and_flatten_verify_canonical_fields_and_widgets(tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    source = tmp_path / "form.pdf"
    out = canvas.Canvas(str(source))
    out.acroForm.textfield(name="name", x=40, y=700, width=200, height=30)
    out.acroForm.checkbox(name="agreed", x=40, y=650, buttonStyle="check")
    out.showPage(); out.save()
    _, filled = run(tmp_path, source, "forms", action="fill", values={"name": "Rodrigo", "agreed": "/Yes"})
    pypdf = pytest.importorskip("pypdf")
    fields = pypdf.PdfReader(filled).get_fields()
    assert fields["name"]["/V"] == "Rodrigo"
    ctx = context(tmp_path); target = tmp_path / "output" / "flat.pdf"
    execute("forms", {"input_path": str(filled), "output_path": str(target), "action": "flatten"}, ctx); ctx.publish()
    assert not pypdf.PdfReader(target).get_fields()
    with fitz.open(target) as doc:
        assert "Rodrigo" in doc[0].get_text()


def test_create_text_and_stamp_page_numbers(tmp_path):
    ctx = context(tmp_path); target = tmp_path / "output" / "new.pdf"
    execute("create", {"text": "Documento com texto estruturado", "output_path": str(target)}, ctx); ctx.publish()
    _, stamped = run(tmp_path, target, "stamp", position="page_number")
    with fitz.open(stamped) as doc:
        assert "1 / 1" in doc[0].get_text()


def test_native_table_extraction_can_export_csv(tmp_path):
    path = tmp_path / "table.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        for x in (30, 130, 230): page.draw_line((x, 30), (x, 130))
        for y in (30, 80, 130): page.draw_line((30, y), (230, y))
        for x, y, value in ((40, 60, "Name"), (140, 60, "Value"), (40, 110, "A"), (140, 110, "10")):
            page.insert_text((x, y), value)
        doc.save(path)
    ctx = context(tmp_path); target = tmp_path / "output" / "tables.csv"
    result = execute("extract_tables", {"input_path": str(path), "output_path": str(target), "format": "csv"}, ctx); ctx.publish()
    assert result["tables"][0]["cells"] == [["Name", "Value"], ["A", "10"]]
    assert "Name,Value" in target.read_text(encoding="utf-8")


def test_validation_uses_independent_pdf_readers(tmp_path, document):
    result = execute("validate", {"input_path": str(document)}, context(tmp_path))
    assert result["structurally_readable"] is True
    assert result["pages"] == 3


def test_signed_document_requires_explicit_acknowledgement_for_transform(tmp_path, document):
    path = tmp_path / "signed.pdf"
    with fitz.open(document) as doc:
        xref = doc.get_new_xref(); doc.update_object(xref, "<< /Type /Sig /ByteRange [0 20 40 10] >>")
        doc.xref_set_key(doc.pdf_catalog(), "ExampleSignature", f"{xref} 0 R"); doc.save(path)
    with pytest.raises(DocumentError) as error:
        execute("transform_pages", {"input_path": str(path), "output_path": str(tmp_path / "output" / "changed.pdf"), "action": "rotate", "angle": 90}, context(tmp_path))
    assert error.value.code == "signed_document"


def test_real_signature_validation_distinguishes_integrity_and_chain_trust(tmp_path, document):
    signers = pytest.importorskip("pyhanko.sign.signers")
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    import datetime

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Onmyoji test signer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .sign(private, hashes.SHA256()))
    bundle = tmp_path / "signer.pfx"
    bundle.write_bytes(pkcs12.serialize_key_and_certificates(b"test", private, cert, None, serialization.BestAvailableEncryption(b"test")))
    trusted = tmp_path / "trusted.pem"; trusted.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    signer = signers.SimpleSigner.load_pkcs12(str(bundle), passphrase=b"test")
    signed = tmp_path / "signed.pdf"
    with document.open("rb") as stream, signed.open("wb") as output:
        signers.PdfSigner(signers.PdfSignatureMetadata(field_name="TestSignature"), signer=signer).sign_pdf(IncrementalPdfFileWriter(stream), output=output)

    result = execute("validate", {"input_path": str(signed), "validation": "signatures"}, context(tmp_path))
    assert result["signatures"][0]["intact"] is True
    assert result["signatures"][0]["cryptographically_valid"] is True
    assert result["signatures"][0]["trusted_chain"] is False
    assert result["signatures"][0]["revocation"] == "unknown"
    result = execute("validate", {"input_path": str(signed), "validation": "signatures", "trust_roots": [str(trusted)]}, context(tmp_path))
    assert result["signatures"][0]["trusted_chain"] is True
