import uuid
from base64 import b64encode
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

import requests_mock
from privates.test import temp_private_root
from rest_framework import status
from rest_framework.test import APITestCase
from vng_api_common.tests import JWTAuthMixin, get_validation_errors, reverse
from vng_api_common.validators import URLValidator
from zds_client.tests.mocks import mock_client

from drc.api.scopes import *
from drc.datamodel.constants import AfzenderTypes, OndertekeningSoorten, Statussen
from drc.datamodel.models import ObjectInformatieObject
from drc.datamodel.tests.factories import (
    EnkelvoudigInformatieObjectCanonicalFactory,
    EnkelvoudigInformatieObjectFactory,
    VerzendingFactory,
)

from ..scopes import (
    SCOPE_DOCUMENTEN_AANMAKEN,
    SCOPE_DOCUMENTEN_ALLES_LEZEN,
    SCOPE_DOCUMENTEN_ALLES_VERWIJDEREN,
    SCOPE_DOCUMENTEN_BIJWERKEN,
    SCOPE_DOCUMENTEN_GEFORCEERD_BIJWERKEN,
    SCOPE_DOCUMENTEN_GEFORCEERD_UNLOCK,
    SCOPE_DOCUMENTEN_LOCK,
)
from .utils import reverse_lazy

INFORMATIEOBJECTTYPE = "https://example.com/informatieobjecttype/foo"
ZAAK = "https://zrc.nl/api/v1/zaken/1234"
BESLUIT = "https://brc.nl/api/v1/besluiten/4321"


class EnkelvoudigInformatieObjectTests(JWTAuthMixin, APITestCase):
    heeft_alle_autorisaties = True

    def assertGegevensGroepRequired(
        self, url: str, field: str, base_body: dict, cases: tuple
    ):
        for key, code in cases:
            with self.subTest(key=key, expected_code=code):
                body = deepcopy(base_body)
                del body[key]
                response = self.client.post(url, {field: body})

                error = get_validation_errors(response, f"{field}.{key}")
                self.assertEqual(error["code"], code)

    def assertGegevensGroepValidation(
        self, url: str, field: str, base_body: dict, cases: tuple
    ):
        for key, code, blank_value in cases:
            with self.subTest(key=key, expected_code=code):
                body = deepcopy(base_body)
                body[key] = blank_value
                response = self.client.post(url, {field: body})

                error = get_validation_errors(response, f"{field}.{key}")
                self.assertEqual(error["code"], code)

    @override_settings(LINK_FETCHER="vng_api_common.mocks.link_fetcher_404")
    def test_validate_informatieobjecttype_invalid_url(self):
        url = reverse("enkelvoudiginformatieobject-list")

        response = self.client.post(url, {"informatieobjecttype": INFORMATIEOBJECTTYPE})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        error = get_validation_errors(response, "informatieobjecttype")
        self.assertEqual(error["code"], URLValidator.code)

    @override_settings(LINK_FETCHER="vng_api_common.mocks.link_fetcher_200")
    def test_validate_informatieobjecttype_invalid_resource(self):
        responses = {INFORMATIEOBJECTTYPE: {"some": "incorrect property"}}

        url = reverse("enkelvoudiginformatieobject-list")

        with mock_client(responses):
            response = self.client.post(
                url, {"informatieobjecttype": INFORMATIEOBJECTTYPE}
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        error = get_validation_errors(response, "informatieobjecttype")
        self.assertEqual(error["code"], "invalid-resource")

    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_validate_informatieobjecttype_unpublished(self, *mocks):
        responses = {
            INFORMATIEOBJECTTYPE: {"url": INFORMATIEOBJECTTYPE, "concept": True}
        }
        url = reverse("enkelvoudiginformatieobject-list")

        with requests_mock.Mocker() as m:
            m.get(INFORMATIEOBJECTTYPE, json=responses[INFORMATIEOBJECTTYPE])
            with mock_client(responses):
                response = self.client.post(
                    url, {"informatieobjecttype": INFORMATIEOBJECTTYPE}
                )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        error = get_validation_errors(response, "informatieobjecttype")
        self.assertEqual(error["code"], "not-published")

    def test_link_fetcher_cannot_connect(self):
        url = reverse("enkelvoudiginformatieobject-list")

        response = self.client.post(
            url,
            {"informatieobjecttype": "http://invalid-host/informatieobjecttype/foo"},
        )

        self.assertNotEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_integriteit(self):
        url = reverse("enkelvoudiginformatieobject-list")

        base_body = {"algoritme": "MD5", "waarde": "foobarbaz", "datum": "2018-12-13"}

        cases = (
            ("algoritme", "required"),
            ("waarde", "required"),
            ("datum", "required"),
        )

        self.assertGegevensGroepRequired(url, "integriteit", base_body, cases)

    def test_integriteit_bad_values(self):
        url = reverse("enkelvoudiginformatieobject-list")

        base_body = {"algoritme": "MD5", "waarde": "foobarbaz", "datum": "2018-12-13"}

        cases = (
            ("algoritme", "invalid_choice", ""),
            ("waarde", "blank", ""),
            ("datum", "null", None),
        )

        self.assertGegevensGroepValidation(url, "integriteit", base_body, cases)

    def test_ondertekening(self):
        url = reverse("enkelvoudiginformatieobject-list")

        base_body = {"soort": OndertekeningSoorten.analoog, "datum": "2018-12-13"}

        cases = (("soort", "required"), ("datum", "required"))

        self.assertGegevensGroepRequired(url, "ondertekening", base_body, cases)

    def test_ondertekening_bad_values(self):
        url = reverse("enkelvoudiginformatieobject-list")

        base_body = {"soort": OndertekeningSoorten.digitaal, "datum": "2018-12-13"}
        cases = (("soort", "invalid_choice", ""), ("datum", "null", None))

        self.assertGegevensGroepValidation(url, "ondertekening", base_body, cases)

    @temp_private_root()
    @override_settings(LINK_FETCHER="vng_api_common.mocks.link_fetcher_200")
    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_inhoud_incorrect_padding(self, *mocks):
        url = reverse("enkelvoudiginformatieobject-list")
        content = {
            "identificatie": uuid.uuid4().hex,
            "bronorganisatie": "159351741",
            "creatiedatum": "2018-06-27",
            "titel": "detailed summary",
            "auteur": "test_auteur",
            "formaat": "txt",
            "taal": "eng",
            "bestandsnaam": "dummy.txt",
            # Remove padding from the base64 data
            "inhoud": b64encode(b"some file content").decode("utf-8")[:-1],
            "bestandsomvang": 17,
            "link": "http://een.link",
            "beschrijving": "test_beschrijving",
            "informatieobjecttype": INFORMATIEOBJECTTYPE,
            "vertrouwelijkheidaanduiding": "openbaar",
        }

        # Send to the API
        response = self.client.post(url, content)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(response, "inhoud")
        self.assertEqual(error["code"], "incorrect-base64-padding")

    @temp_private_root()
    @override_settings(LINK_FETCHER="vng_api_common.mocks.link_fetcher_200")
    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_inhoud_correct_padding(self, *mocks):
        url = reverse("enkelvoudiginformatieobject-list")
        content = {
            "identificatie": uuid.uuid4().hex,
            "bronorganisatie": "159351741",
            "creatiedatum": "2018-06-27",
            "titel": "detailed summary",
            "auteur": "test_auteur",
            "formaat": "txt",
            "taal": "eng",
            "bestandsnaam": "dummy.txt",
            "inhoud": b64encode(b"some file content").decode("utf-8"),
            "bestandsomvang": 17,
            "link": "http://een.link",
            "beschrijving": "test_beschrijving",
            "informatieobjecttype": INFORMATIEOBJECTTYPE,
            "vertrouwelijkheidaanduiding": "openbaar",
        }

        # Send to the API
        response = self.client.post(url, content)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


@override_settings(LINK_FETCHER="vng_api_common.mocks.link_fetcher_200")
class InformatieObjectStatusTests(JWTAuthMixin, APITestCase):
    url = reverse_lazy("enkelvoudiginformatieobject-list")
    # heeft_alle_autorisaties = True
    informatieobjecttype = INFORMATIEOBJECTTYPE
    scopes = [
        SCOPE_DOCUMENTEN_LOCK,
        SCOPE_DOCUMENTEN_AANMAKEN,
        SCOPE_DOCUMENTEN_ALLES_LEZEN,
        SCOPE_DOCUMENTEN_ALLES_VERWIJDEREN,
        SCOPE_DOCUMENTEN_BIJWERKEN,
    ]

    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_ontvangen_informatieobjecten(self, *mocks):
        """
        Assert certain statuses are not allowed for received documents.

        RGBZ 2.00.02 deel II Concept 20180613: De waarden ?in bewerking?
        en ?ter vaststelling? zijn niet van toepassing op ontvangen
        informatieobjecten.
        """
        invalid_statuses = (Statussen.in_bewerking, Statussen.ter_vaststelling)
        data = {
            "bronorganisatie": "319582462",
            "creatiedatum": "2018-12-24",
            "titel": "dummy",
            "auteur": "dummy",
            "taal": "nld",
            "inhoud": "aGVsbG8gd29ybGQ=",
            "bestandsomvang": 17,
            "informatieobjecttype": INFORMATIEOBJECTTYPE,
            "ontvangstdatum": "2018-12-24",
        }

        for invalid_status in invalid_statuses:
            with self.subTest(status=invalid_status):
                _data = data.copy()
                _data["status"] = invalid_status

                response = self.client.post(self.url, _data)

            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            error = get_validation_errors(response, "status")
            self.assertEqual(error["code"], "invalid_for_received")

    def test_informatieobjecten_niet_ontvangen(self):
        """
        All statusses should be allowed when the informatieobject doesn't have
        a receive date.
        """
        for valid_status, _ in Statussen.choices:
            with self.subTest(status=status):
                data = {"ontvangstdatum": None, "status": valid_status}

                response = self.client.post(self.url, data)

            error = get_validation_errors(response, "status")
            self.assertIsNone(error)

    def test_status_set_ontvangstdatum_is_set_later(self):
        """
        Assert that setting the ontvangstdatum later, after an 'invalid' status
        has been set, is not possible.
        """
        eio = EnkelvoudigInformatieObjectFactory.create(
            ontvangstdatum=None, informatieobjecttype=INFORMATIEOBJECTTYPE
        )
        url = reverse("enkelvoudiginformatieobject-detail", kwargs={"uuid": eio.uuid})

        for invalid_status in (Statussen.in_bewerking, Statussen.ter_vaststelling):
            with self.subTest(status=invalid_status):
                eio.status = invalid_status
                eio.save()
                data = {"ontvangstdatum": "2018-12-24"}

                response = self.client.patch(url, data)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                error = get_validation_errors(response, "status")
                self.assertEqual(error["code"], "invalid_for_received")

    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_update_eio_status_definitief_forbidden(self, *mocks):
        eio = EnkelvoudigInformatieObjectFactory.create(
            beschrijving="beschrijving1",
            informatieobjecttype=INFORMATIEOBJECTTYPE,
            status=Statussen.definitief,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail", kwargs={"uuid": eio.uuid}
        )

        eio_response = self.client.get(eio_url)
        eio_data = eio_response.data

        lock = self.client.post(f"{eio_url}/lock").data["lock"]
        eio_data.update(
            {
                "beschrijving": "beschrijving2",
                "inhoud": b64encode(b"aaaaa"),
                "bestandsomvang": 5,
                "lock": lock,
            }
        )

        for i in ["integriteit", "ondertekening"]:
            eio_data.pop(i)

        response = self.client.put(eio_url, eio_data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(response, "nonFieldErrors")
        self.assertEqual(error["code"], "modify-status-definitief")

    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_update_eio_status_definitief_allowed_with_forced_bijwerken(self, *mocks):

        self.autorisatie.scopes += [SCOPE_DOCUMENTEN_GEFORCEERD_BIJWERKEN]
        self.autorisatie.save()

        eio = EnkelvoudigInformatieObjectFactory.create(
            beschrijving="beschrijving1",
            informatieobjecttype=INFORMATIEOBJECTTYPE,
            status=Statussen.definitief,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail", kwargs={"uuid": eio.uuid}
        )
        eio_response = self.client.get(eio_url)
        eio_data = eio_response.data
        lock = self.client.post(f"{eio_url}/lock").data["lock"]
        eio_data.update(
            {
                "beschrijving": "beschrijving2",
                "inhoud": b64encode(b"aaaaa"),
                "bestandsomvang": 5,
                "lock": lock,
            }
        )

        for i in ["integriteit", "ondertekening"]:
            eio_data.pop(i)

        response = self.client.put(eio_url, eio_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("vng_api_common.validators.fetcher")
    @patch("vng_api_common.validators.obj_has_shape", return_value=True)
    def test_update_eio_old_version_forbidden_if_latest_version_is_definitief(
        self, *mocks
    ):
        eio = EnkelvoudigInformatieObjectFactory.create(
            beschrijving="beschrijving1", informatieobjecttype=INFORMATIEOBJECTTYPE
        )

        eio2 = EnkelvoudigInformatieObjectFactory.create(
            canonical=eio.canonical,
            versie=2,
            beschrijving="beschrijving1",
            informatieobjecttype=INFORMATIEOBJECTTYPE,
            status=Statussen.definitief,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail", kwargs={"uuid": eio.uuid}
        )

        eio_response = self.client.get(eio_url)
        eio_data = eio_response.data

        lock = self.client.post(f"{eio_url}/lock").data["lock"]
        eio_data.update(
            {
                "beschrijving": "beschrijving2",
                "inhoud": b64encode(b"aaaaa"),
                "bestandsomvang": 5,
                "lock": lock,
            }
        )

        for i in ["integriteit", "ondertekening"]:
            eio_data.pop(i)

        response = self.client.put(eio_url, eio_data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(response, "nonFieldErrors")
        self.assertEqual(error["code"], "modify-status-definitief")


class FilterValidationTests(JWTAuthMixin, APITestCase):
    """
    Test that incorrect filter usage results in HTTP 400.
    """

    heeft_alle_autorisaties = True

    def test_oio_invalid_filters(self):
        url = reverse("objectinformatieobject-list")

        invalid_filters = {
            "object": "123",  # must be url
            "informatieobject": "123",  # must be url
            "foo": "bar",  # unknown
        }

        for key, value in invalid_filters.items():
            with self.subTest(query_param=key, value=value):
                response = self.client.get(
                    url, {key: value}, HTTP_ACCEPT_CRS="EPSG:4326"
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(LINK_FETCHER="vng_api_common.mocks.link_fetcher_200")
class ObjectInformatieObjectValidationTests(JWTAuthMixin, APITestCase):
    heeft_alle_autorisaties = True

    list_url = reverse(ObjectInformatieObject)

    @patch("vng_api_common.validators.obj_has_shape", return_value=False)
    def test_create_oio_invalid_resource_zaak(self, *mocks):
        eio = EnkelvoudigInformatieObjectFactory.create()
        eio_url = reverse(
            "enkelvoudiginformatieobject-detail", kwargs={"uuid": eio.uuid}
        )

        response = self.client.post(
            self.list_url,
            {
                "object": ZAAK,
                "informatieobject": f"http://testserver{eio_url}",
                "objectType": "zaak",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(response, "object")
        self.assertEqual(error["code"], "invalid-resource")

    @patch("vng_api_common.validators.obj_has_shape", return_value=False)
    def test_create_oio_invalid_resource_besluit(self, *mocks):
        eio = EnkelvoudigInformatieObjectFactory.create()
        eio_url = reverse(
            "enkelvoudiginformatieobject-detail", kwargs={"uuid": eio.uuid}
        )

        response = self.client.post(
            self.list_url,
            {
                "object": BESLUIT,
                "informatieobject": f"http://testserver{eio_url}",
                "objectType": "besluit",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(response, "object")
        self.assertEqual(error["code"], "invalid-resource")


class VerzendingTests(JWTAuthMixin, APITestCase):
    heeft_alle_autorisaties = True

    def test_multiple_addressess_create(self):
        eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"uuid": eio.latest_version.uuid},
        )

        response = self.client.post(
            reverse("verzending-list"),
            {
                "betrokkene": "https://foo.com/persoonX",
                "informatieobject": eio_url,
                "aardRelatie": AfzenderTypes.geadresseerde,
                "toelichting": "Verzending van XYZ",
                "ontvangstdatum": (timezone.now() - timedelta(days=3)).strftime(
                    "%Y-%m-%d"
                ),
                "verzenddatum": timezone.now().strftime("%Y-%m-%d"),
                "contactPersoon": "https://foo.com/persoonY",
                "contactpersoonnaam": "persoonY",
                "binnenlandsCorrespondentieadres": {
                    "huisletter": "Q",
                    "huisnummer": 1,
                    "huisnummerToevoeging": "XYZ",
                    "naamOpenbareRuimte": "ParkY",
                    "postcode": "1800XY",
                    "woonplaatsnaam": "Alkmaar",
                },
                "buitenlandsCorrespondentieadres": {
                    "adresBuitenland_1": "Adres 1",
                    "adresBuitenland_2": "Adres 2",
                    "adresBuitenland_3": "Adres 3",
                    "landPostadres": "https://foo.com/landY",
                },
                "correspondentiePostadres": {
                    "postBusOfAntwoordnummer": "1",
                    "postadresPostcode": "3322DT",
                    "postadresType": "antwoordnummer",
                    "woonplaatsnaam": "4",
                },
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_multiple_address_types_create_fails(self):
        eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"uuid": eio.latest_version.uuid},
        )

        response = self.client.post(
            reverse("verzending-list"),
            {
                "betrokkene": "https://foo.com/persoonX",
                "informatieobject": eio_url,
                "aardRelatie": AfzenderTypes.geadresseerde,
                "toelichting": "Verzending van XYZ",
                "ontvangstdatum": (timezone.now() - timedelta(days=3)).strftime(
                    "%Y-%m-%d"
                ),
                "verzenddatum": timezone.now().strftime("%Y-%m-%d"),
                "contactPersoon": "https://foo.com/persoonY",
                "contactpersoonnaam": "persoonY",
                "binnenlandsCorrespondentieadres": {
                    "huisletter": "Q",
                    "huisnummer": 1,
                    "huisnummerToevoeging": "XYZ",
                    "naamOpenbareRuimte": "ParkY",
                    "postcode": "1800XY",
                    "woonplaatsnaam": "Alkmaar",
                },
                "emailadres": "test@gmail.com",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_no_address_create(self):
        eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"uuid": eio.latest_version.uuid},
        )

        response = self.client.post(
            reverse("verzending-list"),
            {
                "betrokkene": "https://foo.com/persoonX",
                "informatieobject": eio_url,
                "aardRelatie": AfzenderTypes.geadresseerde,
                "toelichting": "Verzending van XYZ",
                "ontvangstdatum": (timezone.now() - timedelta(days=3)).strftime(
                    "%Y-%m-%d"
                ),
                "verzenddatum": timezone.now().strftime("%Y-%m-%d"),
                "contactPersoon": "https://foo.com/persoonY",
                "contactpersoonnaam": "persoonY",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_create_different_correspondence_types(self):
        eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"uuid": eio.latest_version.uuid},
        )
        for correspondense_type in [
            ("emailadres", "test@gmail.com"),
            ("faxnummer", "2133145"),
            ("mijn_overheid", "True"),
        ]:
            with self.subTest(test=correspondense_type):
                response = self.client.post(
                    reverse("verzending-list"),
                    {
                        "betrokkene": "https://foo.com/persoonX",
                        "informatieobject": eio_url,
                        "aardRelatie": AfzenderTypes.geadresseerde,
                        "toelichting": "Verzending van XYZ",
                        "ontvangstdatum": (timezone.now() - timedelta(days=3)).strftime(
                            "%Y-%m-%d"
                        ),
                        "verzenddatum": timezone.now().strftime("%Y-%m-%d"),
                        "contactPersoon": "https://foo.com/persoonY",
                        "contactpersoonnaam": "persoonY",
                        correspondense_type[0]: correspondense_type[1],
                    },
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_add_address_to_already_existing_address_partial_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )
        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "betrokkene": "https://foo.com/PersoonX",
                "binnenlandsCorrespondentieadres": {
                    "huisletter": "Q",
                    "huisnummer": 1,
                    "huisnummerToevoeging": "XYZ",
                    "naamOpenbareRuimte": "ParkY",
                    "postcode": "1800XY",
                    "woonplaatsnaam": "Alkmaar",
                },
            },
        )

        verzending.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_add_fax_to_already_existing_mijn_overheid_partial_update_fails(self):
        verzending = VerzendingFactory(mijn_overheid=True)

        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {"betrokkene": "https://foo.com/PersoonX", "faxnummer": "1234"},
        )

        verzending.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_add_fax_to_already_existing_mijn_overheid_partial_update(self):
        verzending = VerzendingFactory(mijn_overheid=True)

        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "betrokkene": "https://foo.com/PersoonX",
                "faxnummer": "1234",
                "mijnOverheid": False,
            },
        )

        verzending.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_add_email_to_already_existing_mijn_overheid_partial_update(self):
        verzending = VerzendingFactory(faxnummer="124335")

        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "betrokkene": "https://foo.com/PersoonX",
                "faxnummer": None,
                "emailadres": "test@gmail.com",
            },
        )

        verzending.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_add_address_to_already_existing_address_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )

        new_eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        informatieobject_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"version": "1", "uuid": new_eio.latest_version.uuid},
        )

        response = self.client.put(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "betrokkene": verzending.betrokkene,
                "informatieobject": f"http://testserver{informatieobject_url}",
                "aardRelatie": verzending.aard_relatie,
                "toelichting": verzending.toelichting,
                "ontvangstdatum": verzending.ontvangstdatum,
                "verzenddatum": verzending.verzenddatum,
                "contactPersoon": verzending.contact_persoon,
                "contactpersoonnaam": verzending.contactpersoonnaam,
                "binnenlandsCorrespondentieadres": {
                    "huisletter": "Q",
                    "huisnummer": 1,
                    "huisnummerToevoeging": "XYZ",
                    "naamOpenbareRuimte": "ParkY",
                    "postcode": "1800XY",
                    "woonplaatsnaam": "Alkmaar",
                },
            },
        )

        verzending.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_remove_address_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )
        new_eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        informatieobject_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"version": "1", "uuid": new_eio.latest_version.uuid},
        )

        response = self.client.put(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "betrokkene": verzending.betrokkene,
                "informatieobject": f"http://testserver{informatieobject_url}",
                "aardRelatie": verzending.aard_relatie,
                "toelichting": verzending.toelichting,
                "ontvangstdatum": verzending.ontvangstdatum,
                "verzenddatum": verzending.verzenddatum,
                "contactPersoon": verzending.contact_persoon,
                "contactpersoonnaam": verzending.contactpersoonnaam,
            },
        )

        verzending.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["invalidParams"][0]["code"], "invalid-address")

    def test_change_address_partial_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )
        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "buitenlandsCorrespondentieadres": None,
                "binnenlandsCorrespondentieadres": {
                    "huisnummer": 1,
                    "naamOpenbareRuimte": "ParkY",
                    "woonplaatsnaam": "Alkmaar",
                },
                "verzenddatum": verzending.verzenddatum,
                "contactPersoon": verzending.contact_persoon,
                "contactpersoonnaam": verzending.contactpersoonnaam,
            },
        )

        verzending.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # buitenlandsCorrespondentieadres
        self.assertEqual(
            verzending.buitenlands_correspondentieadres_adres_buitenland_1, ""
        )
        self.assertEqual(verzending.buitenlands_correspondentieadres_land_postadres, "")

        # binnenlandsCorrespondentieadres
        self.assertEqual(verzending.binnenlands_correspondentieadres_huisnummer, 1)
        self.assertEqual(
            verzending.binnenlands_correspondentieadres_naam_openbare_ruimte,
            "ParkY",
        )
        self.assertEqual(
            verzending.binnenlands_correspondentieadres_woonplaats,
            "Alkmaar",
        )

    def test_no_address_change_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )
        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "verzenddatum": verzending.verzenddatum,
                "contactPersoon": verzending.contact_persoon,
                "contactpersoonnaam": verzending.contactpersoonnaam,
            },
        )

        verzending.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # buitenlandsCorrespondentieadres
        self.assertEqual(
            verzending.buitenlands_correspondentieadres_adres_buitenland_1,
            "Breedstraat",
        )
        self.assertEqual(
            verzending.buitenlands_correspondentieadres_land_postadres,
            "https://example.com",
        )

        self.assertEqual(verzending.verzenddatum, verzending.verzenddatum)

    def test_change_same_address_partial_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )

        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "buitenlandsCorrespondentieadres": {
                    "adresBuitenland_1": "Adres 1",
                    "adresBuitenland_2": "",
                    "adresBuitenland_3": "",
                    "landPostadres": "https://foo.com/landY",
                },
            },
        )

        verzending.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(
            verzending.buitenlands_correspondentieadres_adres_buitenland_1, "Adres 1"
        )
        self.assertEqual(
            verzending.buitenlands_correspondentieadres_land_postadres,
            "https://foo.com/landY",
        )

    def test_update(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentieadres_adres_buitenland_1="Breedstraat",
            buitenlands_correspondentieadres_land_postadres="https://example.com",
        )
        new_eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        informatieobject_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"version": "1", "uuid": new_eio.latest_version.uuid},
        )

        response = self.client.put(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "betrokkene": verzending.betrokkene,
                "informatieobject": f"http://testserver{informatieobject_url}",
                "aardRelatie": verzending.aard_relatie,
                "toelichting": verzending.toelichting,
                "ontvangstdatum": verzending.ontvangstdatum,
                "verzenddatum": verzending.verzenddatum,
                "contactPersoon": verzending.contact_persoon,
                "contactpersoonnaam": verzending.contactpersoonnaam,
                "buitenlandsCorrespondentieadres": {
                    "adresBuitenland_1": "Adres 1",
                    "adresBuitenland_2": "Adres 2",
                    "adresBuitenland_3": "Adres 3",
                    "landPostadres": "https://foo.com/landY",
                },
            },
        )

        verzending.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(verzending.informatieobject, new_eio)

    def test_postal_code_validation(self):
        verzending = VerzendingFactory(
            buitenlands_correspondentiepostadres_postadres_postcode="1800XY"
        )

        response = self.client.patch(
            reverse("verzending-detail", kwargs={"uuid": verzending.uuid}),
            {
                "correspondentiePostadres": {
                    "postBusOfAntwoordnummer": verzending.buitenlands_correspondentiepostadres_postbus_of_antwoord_nummer,
                    "postadresPostcode": "18800RR",
                    "postadresType": verzending.buitenlands_correspondentiepostadres_postadrestype,
                    "woonplaatsnaam": verzending.buitenlands_correspondentiepostadres_woonplaats,
                }
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(
            response, "correspondentiePostadres.postadresPostcode"
        )
        self.assertEqual(error["reason"], "Postcode moet 6 tekens lang zijn.")

    def test_required_buitenlands_correspondentieadres(self):
        """
        Test that `adresBuitenland1` is required for the
        `afwijkendBuitenlandsCorrespondentieadresVerzending` gegevensgroeptype.
        """

        eio = EnkelvoudigInformatieObjectCanonicalFactory.create(
            latest_version__creatiedatum="2018-12-24",
            latest_version__informatieobjecttype=INFORMATIEOBJECTTYPE,
        )

        eio_url = reverse(
            "enkelvoudiginformatieobject-detail",
            kwargs={"uuid": eio.latest_version.uuid},
        )

        response = self.client.post(
            reverse("verzending-list"),
            {
                "betrokkene": "https://foo.com/persoonX",
                "informatieobject": eio_url,
                "aardRelatie": AfzenderTypes.geadresseerde,
                "toelichting": "Verzending van XYZ",
                "ontvangstdatum": (timezone.now() - timedelta(days=3)).strftime(
                    "%Y-%m-%d"
                ),
                "verzenddatum": timezone.now().strftime("%Y-%m-%d"),
                "contactPersoon": "https://foo.com/persoonY",
                "contactpersoonnaam": "persoonY",
                "buitenlandsCorrespondentieadres": {
                    "adresBuitenland2": "Adres 2",
                    "adresBuitenland3": "Adres 3",
                    "landPostadres": "https://foo.com/landY",
                },
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        error = get_validation_errors(
            response,
            "buitenlandsCorrespondentieadres.adresBuitenland_1",
        )
        self.assertEqual(error["code"], "required")
