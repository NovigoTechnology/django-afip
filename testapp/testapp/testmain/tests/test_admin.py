from unittest import mock

import pytest
from django.contrib import messages
from django.contrib.admin import site
from django.http import HttpRequest
from django.test import RequestFactory
from django.utils.translation import gettext as _
from factory.django import FileField
from pytest_django.asserts import assertContains
from pytest_django.asserts import assertNotContains

from django_afip import exceptions
from django_afip import factories
from django_afip import models
from django_afip.admin import ReceiptAdmin  # type: ignore
from django_afip.admin import catch_errors  # type: ignore


def test_certificate_expired():
    admin = mock.MagicMock()
    request = HttpRequest()

    with catch_errors(admin, request):
        raise exceptions.CertificateExpired

    assert admin.message_user.call_count == 1
    assert admin.message_user.call_args == mock.call(
        request,
        _("The AFIP Taxpayer certificate has expired."),
        messages.ERROR,
    )


def test_certificate_untrusted_cert():
    admin = mock.MagicMock()
    request = HttpRequest()

    with catch_errors(admin, request):
        raise exceptions.UntrustedCertificate

    assert admin.message_user.call_count == 1
    assert admin.message_user.call_args == mock.call(
        request,
        _("The AFIP Taxpayer certificate is untrusted."),
        messages.ERROR,
    )


def test_certificate_auth_error():
    admin = mock.MagicMock()
    request = HttpRequest()

    with catch_errors(admin, request):
        raise exceptions.AuthenticationError

    assert admin.message_user.call_count == 1
    assert admin.message_user.call_args == mock.call(
        request,
        _("An unknown authentication error has ocurred: "),
        messages.ERROR,
    )


def test_without_key(admin_client):
    taxpayer = factories.TaxPayerFactory(key=None)

    response = admin_client.post(
        "/admin/afip/taxpayer/",
        data={"_selected_action": [taxpayer.id], "action": "generate_key"},
        follow=True,
    )

    assert response.status_code == 200
    assertContains(response, "Key generated successfully.")

    taxpayer.refresh_from_db()
    assert "-----BEGIN PRIVATE KEY-----" in taxpayer.key.file.read().decode()


def test_with_key(admin_client):
    taxpayer = factories.TaxPayerFactory(key=FileField(data=b"Blah"))

    response = admin_client.post(
        "/admin/afip/taxpayer/",
        data={"_selected_action": [taxpayer.id], "action": "generate_key"},
        follow=True,
    )

    assert response.status_code == 200
    assertContains(
        response,
        "No keys generated; Taxpayers already had keys.",
    )

    taxpayer.refresh_from_db()
    assert "Blah" == taxpayer.key.file.read().decode()


def test_admin_taxpayer_request_generation_with_csr(admin_client):
    taxpayer = factories.TaxPayerFactory(key=None)
    taxpayer.generate_key()

    response = admin_client.post(
        "/admin/afip/taxpayer/",
        data={"_selected_action": [taxpayer.id], "action": "generate_csr"},
        follow=True,
    )

    assert response.status_code == 200
    assert (
        b"Content-Type: application/pkcs10" in response.serialize_headers().splitlines()
    )
    assertContains(response, "-----BEGIN CERTIFICATE REQUEST-----")


def test_admin_taxpayer_request_generation_without_key(admin_client):
    taxpayer = factories.TaxPayerFactory(key=None)
    taxpayer.generate_key()

    response = admin_client.post(
        "/admin/afip/taxpayer/",
        data={"_selected_action": [taxpayer.id], "action": "generate_csr"},
        follow=True,
    )

    assert response.status_code == 200
    assert (
        b"Content-Type: application/pkcs10" in response.serialize_headers().splitlines()
    )
    assertContains(response, "-----BEGIN CERTIFICATE REQUEST-----")


def test_admin_taxpayer_request_generation_multiple_taxpayers(admin_client):
    taxpayer1 = factories.TaxPayerFactory(key__data=b"Blah")
    taxpayer2 = factories.TaxPayerFactory(key__data=b"Blah")

    response = admin_client.post(
        "/admin/afip/taxpayer/",
        data={
            "_selected_action": [taxpayer1.id, taxpayer2.id],
            "action": "generate_csr",
        },
        follow=True,
    )

    assert response.status_code == 200
    assertContains(response, "Can only generate CSR for one taxpayer at a time")


def test_validation_filters(admin_client):
    """Test the admin validation filters.

    This filters receipts by the validation status.
    """
    validated_receipt = factories.ReceiptFactory()
    failed_validation_receipt = factories.ReceiptFactory()
    not_validated_receipt = factories.ReceiptFactory()

    factories.ReceiptValidationFactory(receipt=validated_receipt)
    factories.ReceiptValidationFactory(
        result=models.ReceiptValidation.RESULT_REJECTED,
        receipt=failed_validation_receipt,
    )

    response = admin_client.get("/admin/afip/receipt/?status=validated")

    assertContains(
        response,
        '<input class="action-select" name="_selected_action" value="{}" '
        'type="checkbox">'.format(validated_receipt.pk),
        html=True,
    )
    assertNotContains(
        response,
        '<input class="action-select" name="_selected_action" value="{}" '
        'type="checkbox">'.format(not_validated_receipt.pk),
        html=True,
    )
    assertNotContains(
        response,
        '<input class="action-select" name="_selected_action" value="{}" '
        'type="checkbox">'.format(failed_validation_receipt.pk),
        html=True,
    )

    response = admin_client.get("/admin/afip/receipt/?status=not_validated")
    assertNotContains(
        response,
        '<input class="action-select" name="_selected_action" value="{}" '
        'type="checkbox">'.format(validated_receipt.pk),
        html=True,
    )
    assertContains(
        response,
        '<input class="action-select" name="_selected_action" value="{}" '
        'type="checkbox">'.format(not_validated_receipt.pk),
        html=True,
    )
    assertContains(
        response,
        '<input class="action-select" name="_selected_action" value="{}" '
        'type="checkbox">'.format(failed_validation_receipt.pk),
        html=True,
    )


@pytest.mark.django_db
def test_receipt_admin_get_exclude():
    admin = ReceiptAdmin(models.Receipt, site)
    request = RequestFactory().get("/admin/afip/receipt")
    request.user = factories.UserFactory()

    assert "related_receipts" in admin.get_fields(request)


@pytest.mark.django_db
def create_test_receipt_pdfs():
    validation = factories.ReceiptValidationFactory()
    with_file = factories.ReceiptPDFFactory(receipt=validation.receipt)
    without_file = factories.ReceiptPDFFactory()

    assert not without_file.pdf_file
    assert with_file.pdf_file

    return with_file, without_file


def test_has_file_filter_all(admin_client):
    """Check that the has_file filter applies properly

    In order to confirm that it's working, we check that the link to the
    object's change page is present, since no matter how we reformat the rows,
    this will always be present as long as the object is listed.
    """
    with_file, without_file = create_test_receipt_pdfs()

    response = admin_client.get("/admin/afip/receiptpdf/")
    assertContains(response, f"/admin/afip/receiptpdf/{with_file.pk}/change/")
    assertContains(response, f"/admin/afip/receiptpdf/{without_file.pk}/change/")


def test_has_file_filter_with_file(admin_client):
    with_file, without_file = create_test_receipt_pdfs()

    response = admin_client.get("/admin/afip/receiptpdf/?has_file=yes")
    assertContains(response, f"/admin/afip/receiptpdf/{with_file.pk}/change/")
    assertNotContains(response, f"/admin/afip/receiptpdf/{without_file.pk}/change/")


def test_has_file_filter_without_file(admin_client):
    with_file, without_file = create_test_receipt_pdfs()

    response = admin_client.get("/admin/afip/receiptpdf/?has_file=no")
    assertNotContains(response, f"/admin/afip/receiptpdf/{with_file.pk}/change/")
    assertContains(response, f"/admin/afip/receiptpdf/{without_file.pk}/change/")
