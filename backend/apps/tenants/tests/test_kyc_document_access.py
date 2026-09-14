"""KYC document upload and download — the most sensitive data in the system.

A tenant document is a scan of a Kenyan national ID or a KRA PIN certificate.
Nothing about it may be reachable by guessing a URL, and nothing that is not a
PDF or an image may be stored, whatever the browser claims the file is.
"""
import datetime as dt
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, APITestCase

from apps.buildings.models import Building, Unit, UnitClassification, UnitStatus
from apps.tenants.models import DocumentType, Tenant, TenantDocument, TenantStatus
from apps.tenants.services import FileValidationError, sanitize_filename, validate_upload

User = get_user_model()

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-1.4"


def _png(size: int = 64) -> bytes:
    return PNG_MAGIC + b"\x00" * size


class KycDocumentAccessTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="o", email="o@test.com", password="testpass123!", role="owner",
        )
        cls.viewer = User.objects.create_user(
            username="v", email="v@test.com", password="testpass123!", role="viewer",
        )
        building = Building.objects.create(name="KYC Block", code="KY", total_floors=1)
        unit = Unit.objects.create(
            building=building, label="KY1", monthly_rent=Decimal("10000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_UNPAID,
        )
        cls.tenant = Tenant.objects.create(
            first_name="Doc", last_name="Owner", id_number="KYC-1",
            phone="+254700000222", unit=unit, monthly_rent=Decimal("10000"),
            move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
        )
        cls.document = TenantDocument.objects.create(
            tenant=cls.tenant,
            doc_type=DocumentType.ID_FRONT,
            file=SimpleUploadedFile("id.png", _png(), content_type="image/png"),
            original_name="id.png",
        )

    def setUp(self):
        self.client = APIClient()

    def _download_url(self, tenant=None, doc=None):
        tenant = tenant or self.tenant
        doc = doc or self.document
        return f"/api/tenants/{tenant.pk}/documents/{doc.pk}/download/"

    # ── Access control ──────────────────────────────────────────────────────

    def test_anonymous_cannot_download_a_kyc_document(self):
        """The whole point: knowing the URL must not be enough."""
        response = self.client.get(self._download_url())
        assert response.status_code == 401

    def test_anonymous_cannot_list_documents(self):
        response = self.client.get(f"/api/tenants/{self.tenant.pk}/documents/list/")
        assert response.status_code == 401

    def test_authenticated_staff_can_download(self):
        """Reads are open to every authenticated role in this single-org product.

        Recorded deliberately: if that ever needs to change — a caretaker having
        no business reading a national ID is a defensible position — this is the
        test to change, and `CanManageTenants.read_roles` is where.
        """
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(self._download_url())
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment")

    def test_a_document_cannot_be_fetched_through_another_tenant(self):
        """IDOR: the doc id is scoped to the tenant in the URL, not global."""
        other_unit = Unit.objects.create(
            building=self.tenant.unit.building, label="KY2",
            monthly_rent=Decimal("9000"), status=UnitStatus.OCCUPIED_UNPAID,
        )
        other = Tenant.objects.create(
            first_name="Other", last_name="Tenant", id_number="KYC-2",
            phone="+254700000333", unit=other_unit, monthly_rent=Decimal("9000"),
            move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self._download_url(tenant=other))
        assert response.status_code == 404

    def test_uploading_requires_the_tenant_capability(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            f"/api/tenants/{self.tenant.pk}/documents/",
            {"file": SimpleUploadedFile("x.png", _png(), content_type="image/png"),
             "doc_type": DocumentType.ID_FRONT},
            format="multipart",
        )
        assert response.status_code == 403

    def test_owner_can_upload(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            f"/api/tenants/{self.tenant.pk}/documents/",
            {"file": SimpleUploadedFile("passport.png", _png(), content_type="image/png"),
             "doc_type": DocumentType.PASSPORT},
            format="multipart",
        )
        assert response.status_code == 201

    # ── Upload validation ───────────────────────────────────────────────────

    def test_a_script_disguised_as_a_png_is_refused(self):
        """The declared content-type and the extension are both attacker-supplied.

        Only the magic bytes are not, which is why `_sniff_content_type` exists.
        """
        self.client.force_authenticate(user=self.owner)
        payload = SimpleUploadedFile(
            "innocent.png",
            b"<?php system($_GET['c']); ?>",
            content_type="image/png",
        )
        response = self.client.post(
            f"/api/tenants/{self.tenant.pk}/documents/",
            {"file": payload, "doc_type": DocumentType.OTHER},
            format="multipart",
        )
        assert response.status_code == 400
        assert "do not match" in response.json()["detail"]

    def test_an_svg_is_refused_even_though_it_is_an_image(self):
        """SVG carries script. It is not on the allowlist and must not sneak in."""
        self.client.force_authenticate(user=self.owner)
        payload = SimpleUploadedFile(
            "logo.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            content_type="image/svg+xml",
        )
        response = self.client.post(
            f"/api/tenants/{self.tenant.pk}/documents/",
            {"file": payload, "doc_type": DocumentType.OTHER},
            format="multipart",
        )
        assert response.status_code == 400

    def test_a_file_over_the_size_limit_is_refused(self):
        oversized = BytesIO(PDF_MAGIC + b"\x00" * (6 * 1024 * 1024))
        upload = SimpleUploadedFile("big.pdf", oversized.getvalue(), content_type="application/pdf")
        try:
            validate_upload(upload)
        except FileValidationError as exc:
            assert "too large" in str(exc).lower()
        else:  # pragma: no cover
            raise AssertionError("a 6 MB upload was accepted")

    # ── Filename handling ───────────────────────────────────────────────────

    def test_path_traversal_in_a_filename_is_stripped(self):
        assert sanitize_filename("../../etc/passwd") == "etc_passwd" or \
               sanitize_filename("../../etc/passwd") == "passwd"
        assert "/" not in sanitize_filename("../../etc/passwd")
        assert "\\" not in sanitize_filename(r"C:\windows\system32\cmd.exe")
        assert not sanitize_filename("...hidden").startswith(".")

    def test_the_stored_name_is_the_sanitized_one_not_the_client_value(self):
        """`original_name` is echoed back in Content-Disposition on download."""
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            f"/api/tenants/{self.tenant.pk}/documents/",
            {"file": SimpleUploadedFile('a"; filename="evil.pdf', _png(), content_type="image/png"),
             "doc_type": DocumentType.OTHER},
            format="multipart",
        )
        assert response.status_code == 201
        stored = response.json()["original_name"]
        assert '"' not in stored
        assert ";" not in stored
