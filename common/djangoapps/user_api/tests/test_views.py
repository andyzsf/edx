"""Tests for the user API at the HTTP request level. """

import datetime
import base64
import json
import re

from django.conf import settings
from django.core.urlresolvers import reverse
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from unittest import SkipTest, skipUnless
import ddt
from pytz import UTC
import mock
from xmodule.modulestore.tests.factories import CourseFactory

from user_api.api import account as account_api, profile as profile_api

from student.tests.factories import UserFactory
from user_api.models import UserOrgTag
from user_api.tests.factories import UserPreferenceFactory
from django_comment_common import models
from opaque_keys.edx.locations import SlashSeparatedCourseKey
from third_party_auth.tests.testutil import simulate_running_pipeline

from user_api.tests.test_constants import SORTED_COUNTRIES


TEST_API_KEY = "test_api_key"
USER_LIST_URI = "/user_api/v1/users/"
USER_PREFERENCE_LIST_URI = "/user_api/v1/user_prefs/"
ROLE_LIST_URI = "/user_api/v1/forum_roles/Moderator/users/"


@override_settings(EDX_API_KEY=TEST_API_KEY)
class ApiTestCase(TestCase):

    LIST_URI = USER_LIST_URI

    def basic_auth(self, username, password):
        return {'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode('%s:%s' % (username, password))}

    def request_with_auth(self, method, *args, **kwargs):
        """Issue a get request to the given URI with the API key header"""
        return getattr(self.client, method)(*args, HTTP_X_EDX_API_KEY=TEST_API_KEY, **kwargs)

    def get_json(self, *args, **kwargs):
        """Make a request with the given args and return the parsed JSON repsonse"""
        resp = self.request_with_auth("get", *args, **kwargs)
        self.assertHttpOK(resp)
        self.assertTrue(resp["Content-Type"].startswith("application/json"))
        return json.loads(resp.content)

    def get_uri_for_user(self, target_user):
        """Given a user object, get the URI for the corresponding resource"""
        users = self.get_json(USER_LIST_URI)["results"]
        for user in users:
            if user["id"] == target_user.id:
                return user["url"]
        self.fail()

    def get_uri_for_pref(self, target_pref):
        """Given a user preference object, get the URI for the corresponding resource"""
        prefs = self.get_json(USER_PREFERENCE_LIST_URI)["results"]
        for pref in prefs:
            if (pref["user"]["id"] == target_pref.user.id and pref["key"] == target_pref.key):
                return pref["url"]
        self.fail()

    def assertAllowedMethods(self, uri, expected_methods):
        """Assert that the allowed methods for the given URI match the expected list"""
        resp = self.request_with_auth("options", uri)
        self.assertHttpOK(resp)
        allow_header = resp.get("Allow")
        self.assertIsNotNone(allow_header)
        allowed_methods = re.split('[^A-Z]+', allow_header)
        self.assertItemsEqual(allowed_methods, expected_methods)

    def assertSelfReferential(self, obj):
        """Assert that accessing the "url" entry in the given object returns the same object"""
        copy = self.get_json(obj["url"])
        self.assertEqual(obj, copy)

    def assertUserIsValid(self, user):
        """Assert that the given user result is valid"""
        self.assertItemsEqual(user.keys(), ["email", "id", "name", "username", "preferences", "url"])
        self.assertItemsEqual(
            user["preferences"].items(),
            [(pref.key, pref.value) for pref in self.prefs if pref.user.id == user["id"]]
        )
        self.assertSelfReferential(user)

    def assertPrefIsValid(self, pref):
        self.assertItemsEqual(pref.keys(), ["user", "key", "value", "url"])
        self.assertSelfReferential(pref)
        self.assertUserIsValid(pref["user"])

    def assertHttpOK(self, response):
        """Assert that the given response has the status code 200"""
        self.assertEqual(response.status_code, 200)

    def assertHttpForbidden(self, response):
        """Assert that the given response has the status code 403"""
        self.assertEqual(response.status_code, 403)

    def assertHttpBadRequest(self, response):
        """Assert that the given response has the status code 400"""
        self.assertEqual(response.status_code, 400)

    def assertHttpMethodNotAllowed(self, response):
        """Assert that the given response has the status code 405"""
        self.assertEqual(response.status_code, 405)

    def assertAuthDisabled(self, method, uri):
        """
        Assert that the Django rest framework does not interpret basic auth
        headers for views exposed to anonymous users as an attempt to authenticate.

        """
        # Django rest framework interprets basic auth headers
        # as an attempt to authenticate with the API.
        # We don't want this for views available to anonymous users.
        basic_auth_header = "Basic " + base64.b64encode('username:password')
        response = getattr(self.client, method)(uri, HTTP_AUTHORIZATION=basic_auth_header)
        self.assertNotEqual(response.status_code, 403)


class EmptyUserTestCase(ApiTestCase):
    def test_get_list_empty(self):
        result = self.get_json(self.LIST_URI)
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["next"])
        self.assertIsNone(result["previous"])
        self.assertEqual(result["results"], [])


class EmptyRoleTestCase(ApiTestCase):
    """Test that the endpoint supports empty result sets"""
    course_id = SlashSeparatedCourseKey.from_deprecated_string("org/course/run")
    LIST_URI = ROLE_LIST_URI + "?course_id=" + course_id.to_deprecated_string()

    def test_get_list_empty(self):
        """Test that the endpoint properly returns empty result sets"""
        result = self.get_json(self.LIST_URI)
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["next"])
        self.assertIsNone(result["previous"])
        self.assertEqual(result["results"], [])


class UserApiTestCase(ApiTestCase):
    def setUp(self):
        super(UserApiTestCase, self).setUp()
        self.users = [
            UserFactory.create(
                email="test{0}@test.org".format(i),
                profile__name="Test {0}".format(i)
            )
            for i in range(5)
        ]
        self.prefs = [
            UserPreferenceFactory.create(user=self.users[0], key="key0"),
            UserPreferenceFactory.create(user=self.users[0], key="key1"),
            UserPreferenceFactory.create(user=self.users[1], key="key0")
        ]


class RoleTestCase(UserApiTestCase):
    course_id = SlashSeparatedCourseKey.from_deprecated_string("org/course/run")
    LIST_URI = ROLE_LIST_URI + "?course_id=" + course_id.to_deprecated_string()

    def setUp(self):
        super(RoleTestCase, self).setUp()
        (role, _) = models.Role.objects.get_or_create(
            name=models.FORUM_ROLE_MODERATOR,
            course_id=self.course_id
        )
        for user in self.users:
            user.roles.add(role)

    def test_options_list(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_post_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.LIST_URI))

    def test_put_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_list_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_delete_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_list_unauthorized(self):
        self.assertHttpForbidden(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpOK(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=False)
    @override_settings(EDX_API_KEY=TEST_API_KEY)
    def test_basic_auth(self):
        # ensure that having basic auth headers in the mix does not break anything
        self.assertHttpOK(
            self.request_with_auth("get", self.LIST_URI,
                                   **self.basic_auth("someuser", "somepass")))
        self.assertHttpForbidden(
            self.client.get(self.LIST_URI, **self.basic_auth("someuser", "somepass")))

    def test_get_list_nonempty(self):
        result = self.get_json(self.LIST_URI)
        users = result["results"]
        self.assertEqual(result["count"], len(self.users))
        self.assertEqual(len(users), len(self.users))
        self.assertIsNone(result["next"])
        self.assertIsNone(result["previous"])
        for user in users:
            self.assertUserIsValid(user)

    def test_required_parameter(self):
        response = self.request_with_auth("get", ROLE_LIST_URI)
        self.assertHttpBadRequest(response)

    def test_get_list_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={
            "page_size": 3,
            "course_id": self.course_id.to_deprecated_string(),
        })
        self.assertEqual(first_page["count"], 5)
        first_page_next_uri = first_page["next"]
        self.assertIsNone(first_page["previous"])
        first_page_users = first_page["results"]
        self.assertEqual(len(first_page_users), 3)

        second_page = self.get_json(first_page_next_uri)
        self.assertEqual(second_page["count"], 5)
        self.assertIsNone(second_page["next"])
        second_page_prev_uri = second_page["previous"]
        second_page_users = second_page["results"]
        self.assertEqual(len(second_page_users), 2)

        self.assertEqual(self.get_json(second_page_prev_uri), first_page)

        for user in first_page_users + second_page_users:
            self.assertUserIsValid(user)
        all_user_uris = [user["url"] for user in first_page_users + second_page_users]
        self.assertEqual(len(set(all_user_uris)), 5)


class UserViewSetTest(UserApiTestCase):
    LIST_URI = USER_LIST_URI

    def setUp(self):
        super(UserViewSetTest, self).setUp()
        self.detail_uri = self.get_uri_for_user(self.users[0])

    # List view tests

    def test_options_list(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_post_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.LIST_URI))

    def test_put_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_list_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_delete_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_list_unauthorized(self):
        self.assertHttpForbidden(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpOK(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=False)
    @override_settings(EDX_API_KEY=TEST_API_KEY)
    def test_basic_auth(self):
        # ensure that having basic auth headers in the mix does not break anything
        self.assertHttpOK(
            self.request_with_auth("get", self.LIST_URI,
                                   **self.basic_auth('someuser', 'somepass')))
        self.assertHttpForbidden(
            self.client.get(self.LIST_URI, **self.basic_auth('someuser', 'somepass')))

    def test_get_list_nonempty(self):
        result = self.get_json(self.LIST_URI)
        self.assertEqual(result["count"], 5)
        self.assertIsNone(result["next"])
        self.assertIsNone(result["previous"])
        users = result["results"]
        self.assertEqual(len(users), 5)
        for user in users:
            self.assertUserIsValid(user)

    def test_get_list_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={"page_size": 3})
        self.assertEqual(first_page["count"], 5)
        first_page_next_uri = first_page["next"]
        self.assertIsNone(first_page["previous"])
        first_page_users = first_page["results"]
        self.assertEqual(len(first_page_users), 3)

        second_page = self.get_json(first_page_next_uri)
        self.assertEqual(second_page["count"], 5)
        self.assertIsNone(second_page["next"])
        second_page_prev_uri = second_page["previous"]
        second_page_users = second_page["results"]
        self.assertEqual(len(second_page_users), 2)

        self.assertEqual(self.get_json(second_page_prev_uri), first_page)

        for user in first_page_users + second_page_users:
            self.assertUserIsValid(user)
        all_user_uris = [user["url"] for user in first_page_users + second_page_users]
        self.assertEqual(len(set(all_user_uris)), 5)

    # Detail view tests

    def test_options_detail(self):
        self.assertAllowedMethods(self.detail_uri, ["OPTIONS", "GET", "HEAD"])

    def test_post_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.detail_uri))

    def test_put_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.detail_uri))

    def test_patch_detail_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_delete_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.detail_uri))

    def test_get_detail_unauthorized(self):
        self.assertHttpForbidden(self.client.get(self.detail_uri))

    def test_get_detail(self):
        user = self.users[1]
        uri = self.get_uri_for_user(user)
        self.assertEqual(
            self.get_json(uri),
            {
                "email": user.email,
                "id": user.id,
                "name": user.profile.name,
                "username": user.username,
                "preferences": dict([
                    (user_pref.key, user_pref.value)
                    for user_pref in self.prefs
                    if user_pref.user == user
                ]),
                "url": uri
            }
        )


class UserPreferenceViewSetTest(UserApiTestCase):
    LIST_URI = USER_PREFERENCE_LIST_URI

    def setUp(self):
        super(UserPreferenceViewSetTest, self).setUp()
        self.detail_uri = self.get_uri_for_pref(self.prefs[0])

    # List view tests

    def test_options_list(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_put_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_list_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_delete_list_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_list_unauthorized(self):
        self.assertHttpForbidden(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpOK(self.client.get(self.LIST_URI))

    def test_get_list_nonempty(self):
        result = self.get_json(self.LIST_URI)
        self.assertEqual(result["count"], 3)
        self.assertIsNone(result["next"])
        self.assertIsNone(result["previous"])
        prefs = result["results"]
        self.assertEqual(len(prefs), 3)
        for pref in prefs:
            self.assertPrefIsValid(pref)

    def test_get_list_filter_key_empty(self):
        result = self.get_json(self.LIST_URI, data={"key": "non-existent"})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["results"], [])

    def test_get_list_filter_key_nonempty(self):
        result = self.get_json(self.LIST_URI, data={"key": "key0"})
        self.assertEqual(result["count"], 2)
        prefs = result["results"]
        self.assertEqual(len(prefs), 2)
        for pref in prefs:
            self.assertPrefIsValid(pref)
            self.assertEqual(pref["key"], "key0")

    def test_get_list_filter_user_empty(self):
        def test_id(user_id):
            result = self.get_json(self.LIST_URI, data={"user": user_id})
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["results"], [])
        test_id(self.users[2].id)
        # TODO: If the given id does not match a user, then the filter is a no-op
        # test_id(42)
        # test_id("asdf")

    def test_get_list_filter_user_nonempty(self):
        user_id = self.users[0].id
        result = self.get_json(self.LIST_URI, data={"user": user_id})
        self.assertEqual(result["count"], 2)
        prefs = result["results"]
        self.assertEqual(len(prefs), 2)
        for pref in prefs:
            self.assertPrefIsValid(pref)
            self.assertEqual(pref["user"]["id"], user_id)

    def test_get_list_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={"page_size": 2})
        self.assertEqual(first_page["count"], 3)
        first_page_next_uri = first_page["next"]
        self.assertIsNone(first_page["previous"])
        first_page_prefs = first_page["results"]
        self.assertEqual(len(first_page_prefs), 2)

        second_page = self.get_json(first_page_next_uri)
        self.assertEqual(second_page["count"], 3)
        self.assertIsNone(second_page["next"])
        second_page_prev_uri = second_page["previous"]
        second_page_prefs = second_page["results"]
        self.assertEqual(len(second_page_prefs), 1)

        self.assertEqual(self.get_json(second_page_prev_uri), first_page)

        for pref in first_page_prefs + second_page_prefs:
            self.assertPrefIsValid(pref)
        all_pref_uris = [pref["url"] for pref in first_page_prefs + second_page_prefs]
        self.assertEqual(len(set(all_pref_uris)), 3)

    # Detail view tests

    def test_options_detail(self):
        self.assertAllowedMethods(self.detail_uri, ["OPTIONS", "GET", "HEAD"])

    def test_post_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("post", self.detail_uri))

    def test_put_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.detail_uri))

    def test_patch_detail_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_delete_detail_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.detail_uri))

    def test_detail_unauthorized(self):
        self.assertHttpForbidden(self.client.get(self.detail_uri))

    def test_get_detail(self):
        pref = self.prefs[1]
        uri = self.get_uri_for_pref(pref)
        self.assertEqual(
            self.get_json(uri),
            {
                "user": {
                    "email": pref.user.email,
                    "id": pref.user.id,
                    "name": pref.user.profile.name,
                    "username": pref.user.username,
                    "preferences": dict([
                        (user_pref.key, user_pref.value)
                        for user_pref in self.prefs
                        if user_pref.user == pref.user
                    ]),
                    "url": self.get_uri_for_user(pref.user),
                },
                "key": pref.key,
                "value": pref.value,
                "url": uri,
            }
        )


class PreferenceUsersListViewTest(UserApiTestCase):
    LIST_URI = "/user_api/v1/preferences/key0/users/"

    def test_options(self):
        self.assertAllowedMethods(self.LIST_URI, ["OPTIONS", "GET", "HEAD"])

    def test_put_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("put", self.LIST_URI))

    def test_patch_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_delete_not_allowed(self):
        self.assertHttpMethodNotAllowed(self.request_with_auth("delete", self.LIST_URI))

    def test_unauthorized(self):
        self.assertHttpForbidden(self.client.get(self.LIST_URI))

    @override_settings(DEBUG=True)
    @override_settings(EDX_API_KEY=None)
    def test_debug_auth(self):
        self.assertHttpOK(self.client.get(self.LIST_URI))

    def test_get_basic(self):
        result = self.get_json(self.LIST_URI)
        self.assertEqual(result["count"], 2)
        self.assertIsNone(result["next"])
        self.assertIsNone(result["previous"])
        users = result["results"]
        self.assertEqual(len(users), 2)
        for user in users:
            self.assertUserIsValid(user)

    def test_get_pagination(self):
        first_page = self.get_json(self.LIST_URI, data={"page_size": 1})
        self.assertEqual(first_page["count"], 2)
        first_page_next_uri = first_page["next"]
        self.assertIsNone(first_page["previous"])
        first_page_users = first_page["results"]
        self.assertEqual(len(first_page_users), 1)

        second_page = self.get_json(first_page_next_uri)
        self.assertEqual(second_page["count"], 2)
        self.assertIsNone(second_page["next"])
        second_page_prev_uri = second_page["previous"]
        second_page_users = second_page["results"]
        self.assertEqual(len(second_page_users), 1)

        self.assertEqual(self.get_json(second_page_prev_uri), first_page)

        for user in first_page_users + second_page_users:
            self.assertUserIsValid(user)
        all_user_uris = [user["url"] for user in first_page_users + second_page_users]
        self.assertEqual(len(set(all_user_uris)), 2)


@ddt.ddt
@skipUnless(settings.ROOT_URLCONF == 'lms.urls', 'Test only valid in lms')
class LoginSessionViewTest(ApiTestCase):
    """Tests for the login end-points of the user API. """

    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"

    def setUp(self):
        super(LoginSessionViewTest, self).setUp()
        self.url = reverse("user_api_login_session")

    @ddt.data("get", "post")
    def test_auth_disabled(self, method):
        self.assertAuthDisabled(method, self.url)

    def test_allowed_methods(self):
        self.assertAllowedMethods(self.url, ["GET", "POST", "HEAD", "OPTIONS"])

    def test_put_not_allowed(self):
        response = self.client.put(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_delete_not_allowed(self):
        response = self.client.delete(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_patch_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_login_form(self):
        # Retrieve the login form
        response = self.client.get(self.url, content_type="application/json")
        self.assertHttpOK(response)

        # Verify that the form description matches what we expect
        form_desc = json.loads(response.content)
        self.assertEqual(form_desc["method"], "post")
        self.assertEqual(form_desc["submit_url"], self.url)
        self.assertEqual(form_desc["fields"], [
            {
                "name": "email",
                "defaultValue": "",
                "type": "email",
                "required": True,
                "label": "Email",
                "placeholder": "username@domain.com",
                "instructions": "The email address you used to register with {platform_name}".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "restrictions": {
                    "min_length": account_api.EMAIL_MIN_LENGTH,
                    "max_length": account_api.EMAIL_MAX_LENGTH
                },
                "errorMessages": {},
            },
            {
                "name": "password",
                "defaultValue": "",
                "type": "password",
                "required": True,
                "label": "Password",
                "placeholder": "",
                "instructions": "",
                "restrictions": {
                    "min_length": account_api.PASSWORD_MIN_LENGTH,
                    "max_length": account_api.PASSWORD_MAX_LENGTH
                },
                "errorMessages": {},
            },
            {
                "name": "remember",
                "defaultValue": False,
                "type": "checkbox",
                "required": False,
                "label": "Remember me",
                "placeholder": "",
                "instructions": "",
                "restrictions": {},
                "errorMessages": {},
            }
        ])

    def test_login(self):
        # Create a test user
        UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)

        # Login
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "password": self.PASSWORD,
        })
        self.assertHttpOK(response)

        # Verify that we logged in successfully by accessing
        # a page that requires authentication.
        response = self.client.get(reverse("dashboard"))
        self.assertHttpOK(response)

    @ddt.data(
        (json.dumps(True), False),
        (json.dumps(False), True),
        (None, True),
    )
    @ddt.unpack
    def test_login_remember_me(self, remember_value, expire_at_browser_close):
        # Create a test user
        UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)

        # Login and remember me
        data = {
            "email": self.EMAIL,
            "password": self.PASSWORD,
        }

        if remember_value is not None:
            data["remember"] = remember_value

        response = self.client.post(self.url, data)
        self.assertHttpOK(response)

        # Verify that the session expiration was set correctly
        self.assertEqual(
            self.client.session.get_expire_at_browser_close(),
            expire_at_browser_close
        )

    def test_invalid_credentials(self):
        # Create a test user
        UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)

        # Invalid password
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "password": "invalid"
        })
        self.assertHttpForbidden(response)

        # Invalid email address
        response = self.client.post(self.url, {
            "email": "invalid@example.com",
            "password": self.PASSWORD,
        })
        self.assertHttpForbidden(response)

    def test_missing_login_params(self):
        # Create a test user
        UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)

        # Missing password
        response = self.client.post(self.url, {
            "email": self.EMAIL,
        })
        self.assertHttpBadRequest(response)

        # Missing email
        response = self.client.post(self.url, {
            "password": self.PASSWORD,
        })
        self.assertHttpBadRequest(response)

        # Missing both email and password
        response = self.client.post(self.url, {})


@ddt.ddt
@skipUnless(settings.ROOT_URLCONF == 'lms.urls', 'Test only valid in lms')
class PasswordResetViewTest(ApiTestCase):
    """Tests of the user API's password reset endpoint. """

    def setUp(self):
        super(PasswordResetViewTest, self).setUp()
        self.url = reverse("user_api_password_reset")

    @ddt.data("get", "post")
    def test_auth_disabled(self, method):
        self.assertAuthDisabled(method, self.url)

    def test_allowed_methods(self):
        self.assertAllowedMethods(self.url, ["GET", "HEAD", "OPTIONS"])

    def test_put_not_allowed(self):
        response = self.client.put(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_delete_not_allowed(self):
        response = self.client.delete(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_patch_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_password_reset_form(self):
        # Retrieve the password reset form
        response = self.client.get(self.url, content_type="application/json")
        self.assertHttpOK(response)

        # Verify that the form description matches what we expect
        form_desc = json.loads(response.content)
        self.assertEqual(form_desc["method"], "post")
        self.assertEqual(form_desc["submit_url"], reverse("password_change_request"))
        self.assertEqual(form_desc["fields"], [
            {
                "name": "email",
                "defaultValue": "",
                "type": "email",
                "required": True,
                "label": "Email",
                "placeholder": "username@domain.com",
                "instructions": "The email address you used to register with {platform_name}".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "restrictions": {
                    "min_length": account_api.EMAIL_MIN_LENGTH,
                    "max_length": account_api.EMAIL_MAX_LENGTH
                },
                "errorMessages": {},
            }
        ])


@ddt.ddt
@skipUnless(settings.ROOT_URLCONF == 'lms.urls', 'Test only valid in lms')
class RegistrationViewTest(ApiTestCase):
    """Tests for the registration end-points of the User API. """

    USERNAME = "bob"
    EMAIL = "bob@example.com"
    PASSWORD = "password"
    NAME = "Bob Smith"
    EDUCATION = "m"
    YEAR_OF_BIRTH = "1998"
    ADDRESS = "123 Fake Street"
    CITY = "Springfield"
    COUNTRY = "us"
    GOALS = "Learn all the things!"

    def setUp(self):
        super(RegistrationViewTest, self).setUp()
        self.url = reverse("user_api_registration")

    @ddt.data("get", "post")
    def test_auth_disabled(self, method):
        self.assertAuthDisabled(method, self.url)

    def test_allowed_methods(self):
        self.assertAllowedMethods(self.url, ["GET", "POST", "HEAD", "OPTIONS"])

    def test_put_not_allowed(self):
        response = self.client.put(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_delete_not_allowed(self):
        response = self.client.delete(self.url)
        self.assertHttpMethodNotAllowed(response)

    def test_patch_not_allowed(self):
        raise SkipTest("Django 1.4's test client does not support patch")

    def test_register_form_default_fields(self):
        no_extra_fields_setting = {}

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                u"name": u"email",
                u"type": u"email",
                u"required": True,
                u"label": u"Email",
                u"placeholder": u"username@domain.com",
                u"restrictions": {
                    "min_length": account_api.EMAIL_MIN_LENGTH,
                    "max_length": account_api.EMAIL_MAX_LENGTH
                },
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                u"name": u"name",
                u"type": u"text",
                u"required": True,
                u"label": u"Full Name",
                u"instructions": u"The name that will appear on your certificates",
                u"restrictions": {
                    "max_length": profile_api.FULL_NAME_MAX_LENGTH,
                },
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                u"name": u"username",
                u"type": u"text",
                u"required": True,
                u"label": u"Username",
                u"instructions": u"The name that will identify you in your courses",
                u"restrictions": {
                    "min_length": account_api.USERNAME_MIN_LENGTH,
                    "max_length": account_api.USERNAME_MAX_LENGTH
                },
            }
        )

        self._assert_reg_field(
            no_extra_fields_setting,
            {
                u"name": u"password",
                u"type": u"password",
                u"required": True,
                u"label": u"Password",
                u"restrictions": {
                    "min_length": account_api.PASSWORD_MIN_LENGTH,
                    "max_length": account_api.PASSWORD_MAX_LENGTH
                },
            }
        )

    def test_register_form_third_party_auth_running(self):
        no_extra_fields_setting = {}

        with simulate_running_pipeline(
            "user_api.views.third_party_auth.pipeline",
            "google-oauth2", email="bob@example.com",
            fullname="Bob", username="Bob123"
        ):
            # Password field should be hidden
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    "name": "password",
                    "type": "hidden",
                    "required": False,
                }
            )

            # Email should be filled in
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    u"name": u"email",
                    u"defaultValue": u"bob@example.com",
                    u"type": u"email",
                    u"required": True,
                    u"label": u"Email",
                    u"placeholder": u"username@domain.com",
                    u"restrictions": {
                        "min_length": account_api.EMAIL_MIN_LENGTH,
                        "max_length": account_api.EMAIL_MAX_LENGTH
                    },
                }
            )

            # Full name should be filled in
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    u"name": u"name",
                    u"defaultValue": u"Bob",
                    u"type": u"text",
                    u"required": True,
                    u"label": u"Full Name",
                    u"instructions": u"The name that will appear on your certificates",
                    u"restrictions": {
                        "max_length": profile_api.FULL_NAME_MAX_LENGTH
                    }
                }
            )

            # Username should be filled in
            self._assert_reg_field(
                no_extra_fields_setting,
                {
                    u"name": u"username",
                    u"defaultValue": u"Bob123",
                    u"type": u"text",
                    u"required": True,
                    u"label": u"Username",
                    u"placeholder": u"",
                    u"instructions": u"The name that will identify you in your courses",
                    u"restrictions": {
                        "min_length": account_api.USERNAME_MIN_LENGTH,
                        "max_length": account_api.USERNAME_MAX_LENGTH
                    }
                }
            )

    def test_register_form_level_of_education(self):
        self._assert_reg_field(
            {"level_of_education": "optional"},
            {
                "name": "level_of_education",
                "type": "select",
                "required": False,
                "label": "Highest Level of Education Completed",
                "options": [
                    {"value": "", "name": "--", "default": True},
                    {"value": "p", "name": "Doctorate"},
                    {"value": "m", "name": "Master's or professional degree"},
                    {"value": "b", "name": "Bachelor's degree"},
                    {"value": "a", "name": "Associate's degree"},
                    {"value": "hs", "name": "Secondary/high school"},
                    {"value": "jhs", "name": "Junior secondary/junior high/middle school"},
                    {"value": "el", "name": "Elementary/primary school"},
                    {"value": "none", "name": "None"},
                    {"value": "other", "name": "Other"},
                ],
            }
        )

    def test_register_form_gender(self):
        self._assert_reg_field(
            {"gender": "optional"},
            {
                "name": "gender",
                "type": "select",
                "required": False,
                "label": "Gender",
                "options": [
                    {"value": "", "name": "--", "default": True},
                    {"value": "m", "name": "Male"},
                    {"value": "f", "name": "Female"},
                    {"value": "o", "name": "Other"},
                ],
            }
        )

    def test_register_form_year_of_birth(self):
        this_year = datetime.datetime.now(UTC).year  # pylint: disable=maybe-no-member
        year_options = (
            [{"value": "", "name": "--", "default": True}] + [
                {"value": unicode(year), "name": unicode(year)}
                for year in range(this_year, this_year - 120, -1)
            ]
        )
        self._assert_reg_field(
            {"year_of_birth": "optional"},
            {
                "name": "year_of_birth",
                "type": "select",
                "required": False,
                "label": "Year of Birth",
                "options": year_options,
            }
        )

    def test_registration_form_mailing_address(self):
        self._assert_reg_field(
            {"mailing_address": "optional"},
            {
                "name": "mailing_address",
                "type": "textarea",
                "required": False,
                "label": "Mailing Address",
            }
        )

    def test_registration_form_goals(self):
        self._assert_reg_field(
            {"goals": "optional"},
            {
                "name": "goals",
                "type": "textarea",
                "required": False,
                "label": "If you'd like, tell us why you're interested in {platform_name}".format(
                    platform_name=settings.PLATFORM_NAME
                )
            }
        )

    def test_registration_form_city(self):
        self._assert_reg_field(
            {"city": "optional"},
            {
                "name": "city",
                "type": "text",
                "required": False,
                "label": "City",
            }
        )

    def test_registration_form_country(self):
        country_options = (
            [{"name": "--", "value": "", "default": True}] +
            [
                {"value": country_code, "name": unicode(country_name)}
                for country_code, country_name in SORTED_COUNTRIES
            ]
        )
        self._assert_reg_field(
            {"country": "required"},
            {
                "label": "Country",
                "name": "country",
                "type": "select",
                "required": True,
                "options": country_options,
            }
        )

    @override_settings(
        MKTG_URLS={"ROOT": "https://www.test.com/", "HONOR": "honor"},
    )
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": True})
    def test_registration_honor_code_mktg_site_enabled(self):
        self._assert_reg_field(
            {"honor_code": "required"},
            {
                "label": "I agree to the {platform_name} <a href=\"https://www.test.com/honor\">Terms of Service and Honor Code</a>.".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} <a href=\"https://www.test.com/honor\">Terms of Service and Honor Code</a>.".format(
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

    @override_settings(MKTG_URLS_LINK_MAP={"HONOR": "honor"})
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": False})
    def test_registration_honor_code_mktg_site_disabled(self):
        self._assert_reg_field(
            {"honor_code": "required"},
            {
                "label": "I agree to the {platform_name} <a href=\"/honor\">Terms of Service and Honor Code</a>.".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} <a href=\"/honor\">Terms of Service and Honor Code</a>.".format(
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

    @override_settings(MKTG_URLS={
        "ROOT": "https://www.test.com/",
        "HONOR": "honor",
        "TOS": "tos",
    })
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": True})
    def test_registration_separate_terms_of_service_mktg_site_enabled(self):
        # Honor code field should say ONLY honor code,
        # not "terms of service and honor code"
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} <a href=\"https://www.test.com/honor\">Honor Code</a>.".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} <a href=\"https://www.test.com/honor\">Honor Code</a>.".format(
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

        # Terms of service field should also be present
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} <a href=\"https://www.test.com/tos\">Terms of Service</a>.".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "name": "terms_of_service",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} <a href=\"https://www.test.com/tos\">Terms of Service</a>.".format(
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

    @override_settings(MKTG_URLS_LINK_MAP={"HONOR": "honor", "TOS": "tos"})
    @mock.patch.dict(settings.FEATURES, {"ENABLE_MKTG_SITE": False})
    def test_registration_separate_terms_of_service_mktg_site_disabled(self):
        # Honor code field should say ONLY honor code,
        # not "terms of service and honor code"
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} <a href=\"/honor\">Honor Code</a>.".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "name": "honor_code",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} <a href=\"/honor\">Honor Code</a>.".format(
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

        # Terms of service field should also be present
        self._assert_reg_field(
            {"honor_code": "required", "terms_of_service": "required"},
            {
                "label": "I agree to the {platform_name} <a href=\"/tos\">Terms of Service</a>.".format(
                    platform_name=settings.PLATFORM_NAME
                ),
                "name": "terms_of_service",
                "defaultValue": False,
                "type": "checkbox",
                "required": True,
                "errorMessages": {
                    "required": "You must agree to the {platform_name} <a href=\"/tos\">Terms of Service</a>.".format(
                        platform_name=settings.PLATFORM_NAME
                    )
                }
            }
        )

    @override_settings(REGISTRATION_EXTRA_FIELDS={
        "level_of_education": "optional",
        "gender": "optional",
        "year_of_birth": "optional",
        "mailing_address": "optional",
        "goals": "optional",
        "city": "optional",
        "country": "required",
        "honor_code": "required",
    })
    def test_field_order(self):
        response = self.client.get(self.url)
        self.assertHttpOK(response)

        # Verify that all fields render in the correct order
        form_desc = json.loads(response.content)
        field_names = [field["name"] for field in form_desc["fields"]]
        self.assertEqual(field_names, [
            "email",
            "name",
            "username",
            "password",
            "city",
            "country",
            "level_of_education",
            "gender",
            "year_of_birth",
            "mailing_address",
            "goals",
            "honor_code",
        ])

    def test_register(self):
        # Create a new registration
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Verify that the user exists
        self.assertEqual(
            account_api.account_info(self.USERNAME),
            {
                "username": self.USERNAME,
                "email": self.EMAIL,
                "is_active": False
            }
        )

        # Verify that the user's full name is set
        profile_info = profile_api.profile_info(self.USERNAME)
        self.assertEqual(profile_info["full_name"], self.NAME)

        # Verify that we've been logged in
        # by trying to access a page that requires authentication
        response = self.client.get(reverse("dashboard"))
        self.assertHttpOK(response)

    @override_settings(REGISTRATION_EXTRA_FIELDS={
        "level_of_education": "optional",
        "gender": "optional",
        "year_of_birth": "optional",
        "mailing_address": "optional",
        "goals": "optional",
        "city": "optional",
        "country": "required",
    })
    def test_register_with_profile_info(self):
        # Register, providing lots of demographic info
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "level_of_education": self.EDUCATION,
            "mailing_address": self.ADDRESS,
            "year_of_birth": self.YEAR_OF_BIRTH,
            "goals": self.GOALS,
            "city": self.CITY,
            "country": self.COUNTRY,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Verify the profile information
        profile_info = profile_api.profile_info(self.USERNAME)
        self.assertEqual(profile_info["level_of_education"], self.EDUCATION)
        self.assertEqual(profile_info["mailing_address"], self.ADDRESS)
        self.assertEqual(profile_info["year_of_birth"], int(self.YEAR_OF_BIRTH))
        self.assertEqual(profile_info["goals"], self.GOALS)
        self.assertEqual(profile_info["city"], self.CITY)
        self.assertEqual(profile_info["country"], self.COUNTRY)

    def test_activation_email(self):
        # Register, which should trigger an activation email
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Verify that the activation email was sent
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertEqual(sent_email.to, [self.EMAIL])
        self.assertEqual(sent_email.subject, "Activate Your edX Account")
        self.assertIn("activate your account", sent_email.body)

    @ddt.data(
        {"email": ""},
        {"email": "invalid"},
        {"name": ""},
        {"username": ""},
        {"username": "a"},
        {"password": ""},
    )
    def test_register_invalid_input(self, invalid_fields):
        # Initially, the field values are all valid
        data = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
        }

        # Override the valid fields, making the input invalid
        data.update(invalid_fields)

        # Attempt to create the account, expecting an error response
        response = self.client.post(self.url, data)
        self.assertHttpBadRequest(response)

    @override_settings(REGISTRATION_EXTRA_FIELDS={"country": "required"})
    @ddt.data("email", "name", "username", "password", "country")
    def test_register_missing_required_field(self, missing_field):
        data = {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "country": self.COUNTRY,
        }

        del data[missing_field]

        # Send a request missing a field
        response = self.client.post(self.url, data)
        self.assertHttpBadRequest(response)

    def test_register_duplicate_email(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same email address
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": "someone_else",
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.content,
            "It looks like {} belongs to an existing account. Try again with a different email address.".format(
                self.EMAIL
            )
        )

    def test_register_duplicate_username(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username
        response = self.client.post(self.url, {
            "email": "someone+else@example.com",
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.content,
            "It looks like {} belongs to an existing account. Try again with a different username.".format(
                self.USERNAME
            )
        )

    def test_register_duplicate_username_and_email(self):
        # Register the first user
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": self.NAME,
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertHttpOK(response)

        # Try to create a second user with the same username
        response = self.client.post(self.url, {
            "email": self.EMAIL,
            "name": "Someone Else",
            "username": self.USERNAME,
            "password": self.PASSWORD,
            "honor_code": "true",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.content,
            "It looks like {} and {} belong to an existing account. Try again with a different email address and username.".format(
                self.EMAIL, self.USERNAME
            )
        )

    def _assert_reg_field(self, extra_fields_setting, expected_field):
        """Retrieve the registration form description from the server and
        verify that it contains the expected field.

        Args:
            extra_fields_setting (dict): Override the Django setting controlling
                which extra fields are displayed in the form.

            expected_field (dict): The field definition we expect to find in the form.

        Raises:
            AssertionError

        """
        # Add in fields that are always present
        defaults = [
            ("label", ""),
            ("instructions", ""),
            ("placeholder", ""),
            ("defaultValue", ""),
            ("restrictions", {}),
            ("errorMessages", {}),
        ]
        for key, value in defaults:
            if key not in expected_field:
                expected_field[key] = value

        # Retrieve the registration form description
        with override_settings(REGISTRATION_EXTRA_FIELDS=extra_fields_setting):
            response = self.client.get(self.url)
            self.assertHttpOK(response)

        # Verify that the form description matches what we'd expect
        form_desc = json.loads(response.content)
        self.assertIn(expected_field, form_desc["fields"])


@ddt.ddt
class UpdateEmailOptInTestCase(ApiTestCase):
    """Tests the UpdateEmailOptInPreference view. """

    USERNAME = "steve"
    EMAIL = "steve@isawesome.com"
    PASSWORD = "steveopolis"

    def setUp(self):
        """ Create a course and user, then log in. """
        super(UpdateEmailOptInTestCase, self).setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)
        self.url = reverse("preferences_email_opt_in")

    @ddt.data(
        (u"True", u"True"),
        (u"true", u"True"),
        (u"TrUe", u"True"),
        (u"Banana", u"False"),
        (u"strawberries", u"False"),
        (u"False", u"False"),
    )
    @ddt.unpack
    def test_update_email_opt_in(self, opt, result):
        """Tests the email opt in preference"""
        # Register, which should trigger an activation email
        response = self.client.post(self.url, {
            "course_id": unicode(self.course.id),
            "email_opt_in": opt
        })
        self.assertHttpOK(response)
        preference = UserOrgTag.objects.get(
            user=self.user, org=self.course.id.org, key="email-optin"
        )
        self.assertEquals(preference.value, result)

    @ddt.data(
        (True, False),
        (False, True),
        (False, False)
    )
    @ddt.unpack
    def test_update_email_opt_in_wrong_params(self, use_course_id, use_opt_in):
        """Tests the email opt in preference"""
        params = {}
        if use_course_id:
            params["course_id"] = unicode(self.course.id)
        if use_opt_in:
            params["email_opt_in"] = u"True"

        response = self.client.post(self.url, params)
        self.assertHttpBadRequest(response)
