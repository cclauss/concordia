# TODO: Add correct copyright header

import json
import time
from datetime import datetime, timedelta
from unittest import expectedFailure

from django.test import TestCase
from django.urls import reverse
from rest_framework import status

from concordia.models import (
    MediaType,
    PageInUse,
    Status,
    Tag,
    Transcription,
    User,
    UserAssetTagCollection,
)

from .utils import create_asset


class WebServiceViewTests(TestCase):
    """
    This class contains the unit tests for the view_ws in the concordia app.

    Make sure the postgresql db is available. Run docker-compose up db
    """

    def login_user(self):
        """
        Create a user and log the user in
        """

        # create user and login
        self.user = User.objects.create(username="tester", email="tester@example.com")
        self.user.set_password("top_secret")
        self.user.save()

        self.client.login(username="tester", password="top_secret")

        # create a session cookie
        self.client.session["foo"] = 123  # HACK: needed for django Client

    @expectedFailure
    def test_PageInUse_post(self):
        """
        This unit test tests the post entry for the route ws/page_in_use
        :param self:
        """

        self.login_user()

        response = self.client.post(
            "/ws/page_in_use/",
            {
                "page_url": "http://example.com/campaigns/American-Jerusalem/asset/mamcol.0930/",
                "user": self.user.id,
                "updated_on": datetime.now(),
            },
        )

        self.assert_post_successful(response)

    @expectedFailure
    def test_PageInUse_delete_old_entries_post(self):
        """
        This unit test tests the post entry for the route ws/page_in_use
        """

        self.login_user()

        url = "http://example.com/blah"

        time_threshold = datetime.now() - timedelta(minutes=10)
        page1 = PageInUse(
            page_url=url,
            user=self.user,
            created_on=time_threshold,
            updated_on=time_threshold,
        )
        page1.save()

        page2 = PageInUse(
            page_url=url,
            user=self.user,
            created_on=time_threshold,
            updated_on=time_threshold,
        )
        page2.save()

        response = self.client.post(
            "/ws/page_in_use/",
            {"page_url": url, "user": self.user.id, "updated_on": datetime.now()},
        )

        self.assert_post_successful(response)

    @expectedFailure
    def test_PageInUse_nologin_post(self):
        """
        This unit test tests the post entry for the route ws/page_in_use without
        logging in
        """

        # create user
        self.user = User.objects.create(username="foo", email="tester@example.com")
        self.user.set_password("top_secret")
        self.user.save()

        response = self.client.post(
            "/ws/page_in_use/",
            {
                "page_url": "http://example.com/campaigns/American-Jerusalem/asset/mamcol.0930/",
                "user": self.user.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Verify the entry is not in the PagInUse table
        self.assertEqual(0, PageInUse.objects.count())

    @expectedFailure
    def test_PageInUse_nologin_anonymous_post(self):
        """
        This unit test tests the post entry for the route ws/page_in_use without logging
        and the user is anonymous
        :param self:
        """

        # create user
        self.user = User.objects.create(
            username="anonymous", email="tester@example.com"
        )
        self.user.set_password("top_secret")
        self.user.save()

        response = self.client.post(
            "/ws/page_in_use/",
            {
                "page_url": "campaigns/American-Jerusalem/asset/mamcol.0930/",
                "user": self.user.id,
                "updated_on": datetime.now(),
            },
        )

        self.assert_post_successful(response)

    def assert_post_successful(self, response, expected_record_count=1):
        """
        Check the results of a successful post and insert of a PageInUse database item
        :param response:
        """

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(expected_record_count, PageInUse.objects.count())

    @expectedFailure
    def test_PageInUse_get(self):
        """
        This unit test tests the get entry for the route ws/page_in_use/url
        :param self:
        """

        self.login_user()

        # Add two values to database
        PageInUse.objects.create(page_url="http://example.com/blah", user=self.user)

        page_in_use = PageInUse.objects.create(
            page_url="http://example.com/foobar", user=self.user
        )

        response = self.client.get("/ws/page_in_use/%s/blah/" % page_in_use.page_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertJSONEqual(
            response.content.decode("utf8"),
            {
                "page_url": page_in_use.url,
                "user": self.user.id,
                "updated_on": page_in_use.updated_on.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            },
        )

    def test_PageInUse_put(self):
        """
        This unit test tests the update of an existing PageInUse using PUT on
        route ws/page_in_use/url
        """

        self.login_user()

        # Add a value to database
        page = PageInUse(page_url="http://example.com/blah", user=self.user)
        page.save()

        min_update_time = page.created_on + timedelta(seconds=2)

        # sleep so update time can be tested against original time
        time.sleep(2)

        change_page_in_use = {
            "page_url": "http://example.com/blah",
            "user": self.user.id,
        }

        response = self.client.put(
            "/ws/page_in_use_update/http://example.com/blah/",
            data=json.dumps(change_page_in_use),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        updated_page = PageInUse.objects.filter(page_url="http://example.com/blah")
        self.assertTrue(len(updated_page), 1)
        self.assertEqual(page.id, updated_page[0].id)
        self.assertTrue(updated_page[0].updated_on > min_update_time)

    def test_Transcriptions_latest_get(self):
        """
        Test getting latest transcription for an asset. route ws/transcriptions/asset/
        """

        self.login_user()

        # create a second user
        username = "tester2"
        self.user2 = User.objects.create(username=username, email="tester2@example.com")
        self.user2.set_password("top_secret")
        self.user2.save()

        asset1 = create_asset(
            title="Test Asset 1",
            media_url="http://www.example.com/1/2/3",
            media_type=MediaType.IMAGE,
            status=Status.EDIT,
        )

        # add Transcription objects
        transcription1 = Transcription(
            asset=asset1, user_id=self.user.id, status=Status.EDIT, text="T1"
        )
        transcription1.full_clean()
        transcription1.save()

        transcription2 = Transcription(
            asset=asset1, user_id=self.user2.id, status=Status.EDIT, text="T2"
        )
        transcription2.full_clean()
        transcription2.save()

        response = self.client.get("/ws/transcription/%s/" % asset1.id)

        json_resp = json.loads(response.content)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json_resp["text"], transcription2.text)

    def test_Transcriptions_create_post(self):
        """
        Test creating a transcription. route ws/transcription_create/
        """

        self.login_user()

        asset = create_asset(
            title="TestAsset",
            media_url="http://www.example.com/1/2/3",
            media_type=MediaType.IMAGE,
            status=Status.EDIT,
        )

        response = self.client.post(
            reverse("ws:submit-transcription", kwargs={"asset_pk": asset.pk}),
            {"user_id": self.user.id, "status": Status.EDIT, "text": "T1"},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Get all Transcriptions for the asset
        transcriptions_count = Transcription.objects.filter(asset=asset).count()
        self.assertEqual(transcriptions_count, 1)

        # Add Another transcription
        response = self.client.post(
            reverse("ws:submit-transcription", kwargs={"asset_pk": asset.pk}),
            {"user_id": self.user.id, "status": Status.EDIT, "text": "T2"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Get all Transcriptions for the asset, should be another one
        transcriptions_count = Transcription.objects.filter(asset=asset).count()
        self.assertEqual(transcriptions_count, 2)

    def test_GetTags_get(self):
        """
        Test getting the tags for an asset, route /ws/tags/<asset>
        """

        self.login_user()

        # create a second user
        username = "tester2"
        user2 = User.objects.create(username=username, email="tester2@example.com")
        user2.set_password("top_secret")
        user2.save()

        asset = create_asset(
            title="TestAsset",
            media_url="http://www.example.com/1/2/3",
            media_type=MediaType.IMAGE,
            status=Status.EDIT,
        )

        tag1 = Tag.objects.create(value="Tag1")
        tag2 = Tag.objects.create(value="Tag2")
        tag3 = Tag.objects.create(value="Tag3")

        # Save for User1
        asset_tag_collection = UserAssetTagCollection(asset=asset, user_id=self.user.id)
        asset_tag_collection.save()
        asset_tag_collection.tags.add(tag1, tag2)

        # Save for User2
        asset_tag_collection2 = UserAssetTagCollection(asset=asset, user_id=user2.id)
        asset_tag_collection2.save()
        asset_tag_collection2.tags.add(tag3)

        response = self.client.get(reverse("ws:get-tags", args=(asset.pk,)))

        json_resp = json.loads(response.content)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(json_resp["results"]), 3)

    def test_GetTags_notags_get(self):
        """
        Test getting the tags for an asset when no tags exist, route /ws/tags/<asset>
        """

        self.login_user()

        # create a second user
        username = "tester2"
        user2 = User.objects.create(username=username, email="tester2@example.com")
        user2.set_password("top_secret")
        user2.save()

        asset = create_asset()

        response = self.client.get(
            reverse("ws:submit-tags", kwargs={"asset_pk": asset.id})
        )

        json_resp = json.loads(response.content)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(json_resp["results"]), 0)
