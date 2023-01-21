import json
import os
import secrets
from datetime import datetime
from unittest import skip

import pytz
from constance.test import override_config
from django.test import TestCase
from django_rq import get_worker
from rest_framework.test import APIClient

from api.api_util import get_search_term_examples
from api.date_time_extractor import DEFAULT_RULES_PARAMS, OTHER_RULES_PARAMS

# from api.directory_watcher import scan_photos
from api.models import AlbumAuto, User

# To-Do: Fix setting IMAGE_DIRS and try scanning something
samplephotos_dir = os.path.abspath("samplephotos")


# Create your tests here.
def create_password():
    return secrets.token_urlsafe(10)


class DirTreeTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin", "admin@test.com", create_password()
        )
        self.user = User.objects.create_user("user", "user@test.com", create_password())
        self.client = APIClient()

    def test_admin_should_allow_to_retrieve_dirtree(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/dirtree/")
        self.assertEqual(200, response.status_code)

    def test_regular_user_is_not_allowed_to_retrieve_dirtree(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/dirtree/")
        self.assertEqual(403, response.status_code)

    def test_anonymous_user_is_not_allower_to_retrieve_dirtree(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/dirtree/")
        self.assertEqual(401, response.status_code)


class GetSearchTermExamples(TestCase):
    def test_get_search_term_examples(self):
        admin = User.objects.create_superuser(
            "test_admin", "test_admin@test.com", "test_password"
        )
        array = get_search_term_examples(admin)
        self.assertEqual(len(array), 5)


class RegenerateTitlesTestCase(TestCase):
    def test_regenerate_titles(self):
        admin = User.objects.create_superuser(
            "test_admin", "test_admin@test.com", "test_password"
        )
        # create a album auto
        album_auto = AlbumAuto.objects.create(
            timestamp=datetime.strptime("2022-01-02", "%Y-%m-%d").replace(
                tzinfo=pytz.utc
            ),
            created_on=datetime.strptime("2022-01-02", "%Y-%m-%d").replace(
                tzinfo=pytz.utc
            ),
            owner=admin,
        )
        album_auto._generate_title()
        self.assertEqual(album_auto.title, "Sunday")


class SetupDirectoryTestCase(TestCase):
    userid = 0

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            "test_admin", "test_admin@test.com", create_password()
        )

    def test_setup_directory(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/manage/user/{self.admin.id}/",
            {"scan_directory": "/code"},
        )
        self.assertEqual(response.status_code, 200)

    def test_setup_not_existing_directory(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/manage/user/{self.admin.id}/",
            {"scan_directory": "/non-existent-directory"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), ["Scan directory does not exist"])


@skip("broken")
class ScanPhotosTestCase(TestCase):
    def setUp(self):
        self.client_admin = APIClient()

        self.client_users = [APIClient() for _ in range(2)]

        User.objects.create_superuser(
            "test_admin", "test_admin@test.com", "test_password"
        )
        admin_auth_res = self.client_admin.post(
            "/api/auth/token/obtain/",
            {
                "username": "test_admin",
                "password": "test_password",
            },
        )
        self.client_admin.credentials(
            HTTP_AUTHORIZATION="Bearer " + admin_auth_res.json()["access"]
        )

        # enable signup as admin
        change_settings_res = self.client_admin.post(
            "/api/sitesettings/", {"allow_registration": True}
        )
        self.assertEqual(change_settings_res.json()["allow_registration"], "True")
        self.assertEqual(change_settings_res.status_code, 200)

        logged_in_clients = []

        # sign up 6 test users
        user_ids = []

        for idx, client in enumerate(self.client_users):
            create_user_res = client.post(
                "/api/user/",
                {
                    "email": "test_user_{}@test.com".format(idx),
                    "username": "test_user_{}".format(idx),
                    "password": "test_password",
                },
            )

            self.assertEqual(create_user_res.status_code, 201)
            user_ids.append(create_user_res.json()["id"])

            login_user_res = client.post(
                "/api/auth/token/obtain/",
                {
                    "username": "test_user_{}".format(idx),
                    "password": "test_password",
                },
            )
            self.assertEqual(login_user_res.status_code, 200)

            client.credentials(
                HTTP_AUTHORIZATION="Bearer " + login_user_res.json()["access"]
            )
            logged_in_clients.append(client)
        self.client_users = logged_in_clients

        # set scan directories for each user as admin
        for idx, (user_id, client) in enumerate(zip(user_ids, self.client_users)):
            user_scan_directory = os.path.join(samplephotos_dir, "test{}".format(idx))
            self.assertNotEqual(user_scan_directory, "")
            patch_res = self.client_admin.patch(
                "/api/manage/user/{}/".format(user_id),
                {"scan_directory": user_scan_directory},
            )
            self.assertEqual(patch_res.json(), {})
            self.assertEqual(patch_res.status_code, 200)
            self.assertEqual(patch_res.json()["scan_directory"], user_scan_directory)

        # make sure users are logged in
        for client in self.client_users:
            res = client.get("/api/photos/")
            self.assertEqual(res.status_code, 200)

        # scan photos
        scan_photos_res = self.client_users[0].get("/api/scanphotos/")
        self.assertEqual(scan_photos_res.status_code, 200)
        get_worker().work(burst=True)

        # make sure photos are imported
        get_photos_res = self.client_users[0].get("/api/photos/")
        self.assertEqual(get_photos_res.status_code, 200)
        self.assertTrue(len(get_photos_res.json()["results"]) > 0)

        # try scanning again and make sure there are no duplicate imports
        num_photos = len(get_photos_res.json()["results"])
        scan_photos_res = self.client_users[0].get("/api/scanphotos/")
        self.assertEqual(scan_photos_res.status_code, 200)
        get_worker().work(burst=True)
        get_photos_res = self.client_users[0].get("/api/photos/")
        self.assertEqual(get_photos_res.status_code, 200)
        self.assertEqual(len(get_photos_res.json()["results"]), num_photos)

    def test_auto_albums(self):
        """make sure user can make auto albums, list and retrieve them"""
        # make auto albums
        auto_album_gen_res = self.client_users[0].get("/api/autoalbumgen/")
        self.assertEqual(auto_album_gen_res.status_code, 200)
        get_worker().work(burst=True)

        # make sure auto albums are there
        auto_album_list_res = self.client_users[0].get("/api/albums/auto/list/")
        self.assertEqual(auto_album_list_res.status_code, 200)

        # make sure user can retrieve each auto album
        for album in auto_album_list_res.json()["results"]:
            auto_album_retrieve_res = self.client_users[0].get(
                "/api/albums/auto/%d/" % album["id"]
            )
            self.assertEqual(auto_album_retrieve_res.status_code, 200)
            self.assertTrue(len(auto_album_retrieve_res.json()["photos"]) > 0)

        # try making auto albums again and make sure there are no duplicates
        num_auto_albums = len(auto_album_list_res.json()["results"])

        auto_album_gen_res = self.client_users[0].get("/api/autoalbumgen/")
        self.assertEqual(auto_album_gen_res.status_code, 200)
        get_worker().work(burst=True)

        auto_album_list_res = self.client_users[0].get("/api/albums/auto/list/")
        self.assertEqual(len(auto_album_list_res.json()["results"]), num_auto_albums)

    def test_place_albums(self):
        """make sure user can list and retrieve place albums"""
        place_album_list_res = self.client_users[0].get("/api/albums/place/list/")
        self.assertEqual(place_album_list_res.status_code, 200)

        for album in place_album_list_res.json()["results"]:
            place_album_retrieve_res = self.client_users[0].get(
                "/api/albums/place/%d/" % album["id"]
            )
            self.assertEqual(place_album_retrieve_res.status_code, 200)

    def test_thing_albums(self):
        """make sure user can list and retrieve thing albums"""
        thing_album_list_res = self.client_users[0].get("/api/albums/thing/list/")
        self.assertEqual(thing_album_list_res.status_code, 200)

        for album in thing_album_list_res.json()["results"]:
            thing_album_retrieve_res = self.client_users[0].get(
                "/api/albums/thing/%d/" % album["id"]
            )
            self.assertEqual(thing_album_retrieve_res.status_code, 200)


class PredefinedRulesTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "test_admin", "test_admin@test.com", "test_password"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_predefined_rules(self):
        response = self.client.get("/api/predefinedrules/")
        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertIsInstance(data, str)
        rules = json.loads(data)
        self.assertIsInstance(rules, list)
        self.assertEqual(15, len(rules))

    def test_default_rules(self):
        response = self.client.get("/api/predefinedrules/")
        rules = json.loads(response.json())
        default_rules = list(filter(lambda x: x["is_default"], rules))
        self.assertListEqual(DEFAULT_RULES_PARAMS, default_rules)

    def test_other_rules(self):
        response = self.client.get("/api/predefinedrules/")
        rules = json.loads(response.json())
        other_rules = list(filter(lambda x: not x["is_default"], rules))
        self.assertListEqual(OTHER_RULES_PARAMS, other_rules)


class UserTest(TestCase):
    public_user_properties = [
        "id",
        "avatar_url",
        "username",
        "first_name",
        "last_name",
        "public_photo_count",
        "public_photo_samples",
    ]

    private_user_properties = [
        "id",
        "username",
        "email",
        "scan_directory",
        "confidence",
        "confidence_person",
        "transcode_videos",
        "semantic_search_topk",
        "first_name",
        "public_photo_samples",
        "last_name",
        "public_photo_count",
        "date_joined",
        "avatar",
        "is_superuser",
        "photo_count",
        "nextcloud_server_address",
        "nextcloud_username",
        "nextcloud_scan_directory",
        "avatar_url",
        "favorite_min_rating",
        "image_scale",
        "save_metadata_to_disk",
        "datetime_rules",
        "default_timezone",
        "public_sharing",
    ]

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(
            username="admin",
            first_name="Super",
            last_name="Admin",
            email="admin@test.com",
            password=create_password(),
            is_superuser=True,
            is_staff=True,
        )
        self.user1 = User.objects.create(
            username="user1",
            first_name="Firstname1",
            last_name="Lastname1",
            email="user2@test.com",
            password=create_password(),
            public_sharing=True,
        )
        self.user2 = User.objects.create(
            username="user2",
            first_name="Firstname2",
            last_name="Lastname2",
            email="user2@test.com",
            password=create_password(),
        )

    def test_public_user_list_count(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/user/")
        data = response.json()
        self.assertEquals(
            len(User.objects.filter(public_sharing=True)), len(data["results"])
        )

    def test_public_user_list_properties(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/user/")
        data = response.json()
        for user in data["results"]:
            self.assertEqual(len(self.public_user_properties), len(user.keys()))
            for key in self.public_user_properties:
                self.assertTrue(key in user, f"user does not have key: {key}")

    def test_authenticated_user_list_count(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get("/api/user/")
        data = response.json()
        self.assertEquals(len(User.objects.all()), len(data["results"]))

    def test_authenticated_user_list_properties(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get("/api/user/")
        data = response.json()
        for user in data["results"]:
            self.assertEqual(len(self.private_user_properties), len(user.keys()))
            for key in self.private_user_properties:
                self.assertTrue(key in user, f"user does not have key: {key}")

    def test_user_update_self(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.patch(
            f"/api/user/{self.user1.id}/", data={"first_name": "Updated"}
        )
        self.assertEqual(200, response.status_code)

    def test_public_update_user(self):
        self.client.force_authenticate(user=None)
        response = self.client.patch(
            f"/api/user/{self.user1.id}/", data={"first_name": "Updated"}
        )
        self.assertEqual(401, response.status_code)

    def test_public_delete_user(self):
        self.client.force_authenticate(user=None)
        response = self.client.delete(f"/api/user/{self.user1.id}/")
        self.assertEqual(401, response.status_code)

    @override_config(ALLOW_REGISTRATION=False)
    def test_public_user_create_successful_on_first_setup(self):
        User.objects.all().delete()
        self.client.force_authenticate(user=None)
        data = {
            "username": "super-admin",
            "first_name": "Super",
            "last_name": "Admin",
            "email": "super-admin@test.com",
            "password": create_password(),
        }
        response = self.client.post("/api/user/", data=data)
        self.assertEqual(201, response.status_code)
        self.assertEqual(1, len(User.objects.all()))
        user = User.objects.get(username="super-admin")
        self.assertTrue(user.is_superuser)

    @override_config(ALLOW_REGISTRATION=True)
    def test_public_user_create_successful_when_registration_enabled(self):
        self.client.force_authenticate(user=None)
        data = {
            "username": "new-user",
            "first_name": "NewFirstname",
            "last_name": "NewLastname",
            "email": "new-user@test.com",
            "password": create_password(),
        }
        response = self.client.post("/api/user/", data=data)
        self.assertEqual(201, response.status_code)
        user = User.objects.get(username="new-user")
        self.assertEqual("new-user", user.username)
        self.assertEqual("new-user@test.com", user.email)
        self.assertEqual("NewFirstname", user.first_name)
        self.assertEqual("NewLastname", user.last_name)

    @override_config(ALLOW_REGISTRATION=True)
    def test_after_registration_user_can_authenticate(self):
        self.client.force_authenticate(user=None)
        user = {
            "username": "bart",
            "first_name": "Bart",
            "last_name": "Simpson",
            "email": "bart@test.com",
            "password": create_password(),
        }
        signup_response = self.client.post("/api/user/", data=user)
        self.assertEqual(201, signup_response.status_code)
        login_payload = {
            "username": user["username"],
            "password": user["password"],
        }
        response = self.client.post("/api/auth/token/obtain/", data=login_payload)
        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertTrue("access" in data.keys())
        self.assertTrue("refresh" in data.keys())

    @override_config(ALLOW_REGISTRATION=False)
    def test_public_user_create_fails_when_registration_disabled(self):
        self.client.force_authenticate(user=None)
        data = {
            "username": "another-user",
            "first_name": "NewFirstname",
            "last_name": "NewLastname",
            "email": "another-user@test.com",
            "password": create_password(),
        }
        response = self.client.post("/api/user/", data=data)
        # because IsAdminOrFirstTimeSetupOrRegistrationAllowed is **global** permission
        # on UserViewSet, we are returning 401 and not 403
        self.assertEqual(401, response.status_code)

    @override_config(ALLOW_REGISTRATION=True)
    def test_not_first_setup_create_admin_should_create_regular_user(self):
        self.client.force_authenticate(user=None)
        data = {
            "username": "user3",
            "first_name": "Firstname3",
            "last_name": "Lastname3",
            "email": "user3@test.com",
            "password": create_password(),
            "is_superuser": True,
        }
        response = self.client.post("/api/user/", data=data)
        self.assertEqual(201, response.status_code)
        user = User.objects.get(username="user3")
        self.assertEqual(False, user.is_superuser)

    def test_user_update_another_user(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.patch(
            f"/api/user/{self.user2.id}/", data={"first_name": "Updated"}
        )
        self.assertEqual(403, response.status_code)

    def test_user_delete_another_user(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.delete(f"/api/user/{self.user2.id}/")
        self.assertEqual(403, response.status_code)

    def test_admin_create_user(self):
        self.client.force_authenticate(user=self.admin)
        data = {
            "username": "new-user",
            "password": create_password(),
        }
        response = self.client.post("/api/user/", data=data)
        self.assertEqual(201, response.status_code)

    def test_admin_partial_update_user(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/user/{self.user1.id}/", data={"first_name": "Updated"}
        )
        self.assertEqual(200, response.status_code)

    def test_admin_delete_user(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/user/{self.user1.id}/")
        self.assertEqual(204, response.status_code)

    @override_config(ALLOW_REGISTRATION=False)
    def test_first_time_setup_creates_user_when_registration_is_disabled(self):
        User.objects.all().delete()
        self.client.force_authenticate(user=None)
        data = {
            "username": self.user1.username,
            "first_name": self.user1.first_name,
            "last_name": self.user1.last_name,
            "email": self.user1.email,
            "password": create_password(),
        }
        response = self.client.post("/api/user/", data=data)
        self.assertEqual(201, response.status_code)

    def test_first_time_setup(self):
        User.objects.all().delete()
        response = self.client.get("/api/firsttimesetup/")
        data = response.json()
        self.assertEqual(True, data["isFirstTimeSetup"])

    @override_config(ALLOW_REGISTRATION=True)
    def test_not_first_time_setup(self):
        self.client.force_authenticate(user=None)
        data = {
            "username": "user-name",
            "first_name": "First",
            "last_name": "Last",
            "email": "user-email@test.com",
            "password": create_password(),
        }
        signup_response = self.client.post("/api/user/", data=data)
        self.assertEqual(201, signup_response.status_code)
        user = User.objects.get(username="user-name")
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/firsttimesetup/")
        data = response.json()
        self.assertEqual(False, data["isFirstTimeSetup"])

    def test_regular_user_not_allowed_to_set_scan_directory(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.patch(
            f"/api/user/{self.user1.id}/", {"scan_directory": "/data"}
        )
        data = response.json()
        self.assertNotEqual("/data", data["scan_directory"])
