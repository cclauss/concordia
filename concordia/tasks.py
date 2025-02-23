import datetime
from logging import getLogger

from celery import chord
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from more_itertools.more import chunked

from concordia.models import (
    Asset,
    AssetTranscriptionReservation,
    Campaign,
    CampaignRetirementProgress,
    Item,
    Project,
    SiteReport,
    Tag,
    Topic,
    Transcription,
    UserAssetTagCollection,
    UserRetiredCampaign,
)
from concordia.signals.signals import reservation_released
from concordia.utils import get_anonymous_user

from .celery import app as celery_app

logger = getLogger(__name__)


@celery_app.task
def expire_inactive_asset_reservations():
    timestamp = datetime.datetime.now()

    # Clear old reservations, with a grace period:
    cutoff = timestamp - (
        datetime.timedelta(seconds=2 * settings.TRANSCRIPTION_RESERVATION_SECONDS)
    )

    logger.debug("Clearing reservations with last reserve time older than %s" % cutoff)
    expired_reservations = AssetTranscriptionReservation.objects.filter(
        updated_on__lt=cutoff, tombstoned__in=(None, False)
    )

    for reservation in expired_reservations:
        logger.debug(
            "Expired reservation with token %s" % reservation.reservation_token
        )
        reservation_released.send(
            sender="reserve_asset",
            asset_pk=reservation.asset.pk,
            reservation_token=reservation.reservation_token,
        )
        reservation.delete()


@celery_app.task
def tombstone_old_active_asset_reservations():
    timestamp = datetime.datetime.now()

    cutoff = timestamp - (
        datetime.timedelta(hours=settings.TRANSCRIPTION_RESERVATION_TOMBSTONE_HOURS)
    )

    old_reservations = AssetTranscriptionReservation.objects.filter(
        created_on__lt=cutoff, tombstoned__in=(None, False)
    )
    for reservation in old_reservations:
        logger.debug("Tombstoning reservation %s " % reservation.reservation_token)
        reservation.tombstoned = True
        reservation.save()


@celery_app.task
def delete_old_tombstoned_reservations():
    timestamp = datetime.datetime.now()

    cutoff = timestamp - (
        datetime.timedelta(
            hours=settings.TRANSCRIPTION_RESERVATION_TOMBSTONE_LENGTH_HOURS
        )
    )

    old_reservations = AssetTranscriptionReservation.objects.filter(
        tombstoned__exact=True, updated_on__lt=cutoff
    )
    for reservation in old_reservations:
        logger.debug(
            "Deleting old tombstoned reservation %s" % reservation.reservation_token
        )
        reservation.delete()


@celery_app.task
def site_report():
    report = {
        "assets_not_started": 0,
        "assets_in_progress": 0,
        "assets_submitted": 0,
        "assets_completed": 0,
    }

    asset_count_qs = Asset.objects.values_list("transcription_status").annotate(
        Count("transcription_status")
    )
    for status, count in asset_count_qs:
        logger.debug("Assets %s: %d", status, count)
        report[f"assets_{status}"] = count

    assets_total = Asset.objects.count()
    assets_published = Asset.objects.published().count()
    assets_unpublished = Asset.objects.unpublished().count()

    items_published = Item.objects.published().count()
    items_unpublished = Item.objects.unpublished().count()

    projects_published = Project.objects.published().count()
    projects_unpublished = Project.objects.unpublished().count()

    campaigns_published = Campaign.objects.published().count()
    campaigns_unpublished = Campaign.objects.unpublished().count()

    users_registered = User.objects.all().count()
    users_activated = User.objects.filter(is_active=True).count()

    anonymous_transcriptions = Transcription.objects.filter(
        user=get_anonymous_user()
    ).count()
    transcriptions_saved = Transcription.objects.all().count()

    stats = UserAssetTagCollection.objects.aggregate(Count("tags"))
    tag_count = stats["tags__count"]

    distinct_tag_count = Tag.objects.all().count()

    site_report = SiteReport()
    site_report.assets_total = assets_total
    site_report.assets_published = assets_published
    site_report.assets_not_started = report["assets_not_started"]
    site_report.assets_in_progress = report["assets_in_progress"]
    site_report.assets_waiting_review = report["assets_submitted"]
    site_report.assets_completed = report["assets_completed"]
    site_report.assets_unpublished = assets_unpublished
    site_report.items_published = items_published
    site_report.items_unpublished = items_unpublished
    site_report.projects_published = projects_published
    site_report.projects_unpublished = projects_unpublished
    site_report.anonymous_transcriptions = anonymous_transcriptions
    site_report.transcriptions_saved = transcriptions_saved
    site_report.distinct_tags = distinct_tag_count
    site_report.tag_uses = tag_count
    site_report.campaigns_published = campaigns_published
    site_report.campaigns_unpublished = campaigns_unpublished
    site_report.users_registered = users_registered
    site_report.users_activated = users_activated
    site_report.save()

    for campaign in Campaign.objects.all():
        campaign_report(campaign)

    for topic in Topic.objects.all():
        topic_report(topic)


def topic_report(topic):
    report = {
        "assets_not_started": 0,
        "assets_in_progress": 0,
        "assets_submitted": 0,
        "assets_completed": 0,
    }

    asset_count_qs = (
        Asset.objects.filter(item__project__topics=topic)
        .values_list("transcription_status")
        .annotate(Count("transcription_status"))
    )

    for status, count in asset_count_qs:
        logger.debug("Topic %s assets %s: %d", topic.slug, status, count)
        report[f"assets_{status}"] = count

    assets_total = Asset.objects.filter(item__project__topics=topic).count()
    assets_published = (
        Asset.objects.published().filter(item__project__topics=topic).count()
    )
    assets_unpublished = (
        Asset.objects.unpublished().filter(item__project__topics=topic).count()
    )

    items_published = Item.objects.published().filter(project__topics=topic).count()
    items_unpublished = Item.objects.unpublished().filter(project__topics=topic).count()

    projects_published = Project.objects.published().filter(topics=topic).count()
    projects_unpublished = Project.objects.unpublished().filter(topics=topic).count()

    anonymous_transcriptions = Transcription.objects.filter(
        asset__item__project__topics=topic, user=get_anonymous_user()
    ).count()
    transcriptions_saved = Transcription.objects.filter(
        asset__item__project__topics=topic
    ).count()

    asset_tag_collections = UserAssetTagCollection.objects.filter(
        asset__item__project__topics=topic
    )

    stats = asset_tag_collections.order_by().aggregate(tag_count=Count("tags"))
    tag_count = stats["tag_count"]

    distinct_tag_list = set()

    for tag_collection in asset_tag_collections:
        distinct_tag_list.update(tag_collection.tags.values_list("pk", flat=True))

    distinct_tag_count = len(distinct_tag_list)

    site_report = SiteReport()
    site_report.topic = topic
    site_report.assets_total = assets_total
    site_report.assets_published = assets_published
    site_report.assets_not_started = report["assets_not_started"]
    site_report.assets_in_progress = report["assets_in_progress"]
    site_report.assets_waiting_review = report["assets_submitted"]
    site_report.assets_completed = report["assets_completed"]
    site_report.assets_unpublished = assets_unpublished
    site_report.items_published = items_published
    site_report.items_unpublished = items_unpublished
    site_report.projects_published = projects_published
    site_report.projects_unpublished = projects_unpublished
    site_report.anonymous_transcriptions = anonymous_transcriptions
    site_report.transcriptions_saved = transcriptions_saved
    site_report.distinct_tags = distinct_tag_count
    site_report.tag_uses = tag_count
    site_report.save()


def campaign_report(campaign):
    report = {
        "assets_not_started": 0,
        "assets_in_progress": 0,
        "assets_submitted": 0,
        "assets_completed": 0,
    }

    asset_count_qs = (
        Asset.objects.filter(item__project__campaign=campaign)
        .values_list("transcription_status")
        .annotate(Count("transcription_status"))
    )

    for status, count in asset_count_qs:
        logger.debug("Campaign %s assets %s: %d", campaign.slug, status, count)
        report[f"assets_{status}"] = count

    assets_total = Asset.objects.filter(item__project__campaign=campaign).count()
    assets_published = (
        Asset.objects.published().filter(item__project__campaign=campaign).count()
    )
    assets_unpublished = (
        Asset.objects.unpublished().filter(item__project__campaign=campaign).count()
    )

    items_published = (
        Item.objects.published().filter(project__campaign=campaign).count()
    )
    items_unpublished = (
        Item.objects.published().filter(project__campaign=campaign).count()
    )

    projects_published = Project.objects.published().filter(campaign=campaign).count()
    projects_unpublished = (
        Project.objects.unpublished().filter(campaign=campaign).count()
    )

    anonymous_transcriptions = Transcription.objects.filter(
        asset__item__project__campaign=campaign, user=get_anonymous_user()
    ).count()
    transcriptions_saved = Transcription.objects.filter(
        asset__item__project__campaign=campaign
    ).count()

    asset_tag_collections = UserAssetTagCollection.objects.filter(
        asset__item__project__campaign=campaign
    )

    stats = asset_tag_collections.order_by().aggregate(tag_count=Count("tags"))
    tag_count = stats["tag_count"]

    distinct_tag_list = set()

    for tag_collection in asset_tag_collections:
        distinct_tag_list.update(tag_collection.tags.values_list("pk", flat=True))

    distinct_tag_count = len(distinct_tag_list)

    campaign_assets = Asset.objects.filter(
        item__project__campaign=campaign,
        item__project__published=True,
        item__published=True,
        published=True,
    )
    asset_transcriptions = Transcription.objects.filter(
        asset__in=campaign_assets
    ).values_list("user_id", "reviewed_by")
    user_ids = set(
        [user_id for transcription in asset_transcriptions for user_id in transcription]
    )
    registered_contributor_count = len(user_ids)

    site_report = SiteReport()
    site_report.campaign = campaign
    site_report.assets_total = assets_total
    site_report.assets_published = assets_published
    site_report.assets_not_started = report["assets_not_started"]
    site_report.assets_in_progress = report["assets_in_progress"]
    site_report.assets_waiting_review = report["assets_submitted"]
    site_report.assets_completed = report["assets_completed"]
    site_report.assets_unpublished = assets_unpublished
    site_report.items_published = items_published
    site_report.items_unpublished = items_unpublished
    site_report.projects_published = projects_published
    site_report.projects_unpublished = projects_unpublished
    site_report.anonymous_transcriptions = anonymous_transcriptions
    site_report.transcriptions_saved = transcriptions_saved
    site_report.distinct_tags = distinct_tag_count
    site_report.tag_uses = tag_count
    site_report.registered_contributors = registered_contributor_count
    site_report.save()


@celery_app.task
def calculate_difficulty_values(asset_qs=None):
    """
    Calculate the difficulty scores for the provided AssetQuerySet and update
    the Asset records for changed difficulty values
    """

    if asset_qs is None:
        asset_qs = Asset.objects.published()

    asset_qs = asset_qs.add_contribution_counts()

    updated_count = 0

    # We'll process assets in chunks using an iterator to avoid saving objects
    # which will never be used again in memory. We will find assets which have a
    # difficulty value which is not the same as the value stored in the database
    # and pass them to bulk_update() to be saved in a single query.
    for asset_chunk in chunked(asset_qs.iterator(), 500):
        changed_assets = []

        for asset in asset_chunk:
            difficulty = asset.transcription_count * (
                asset.transcriber_count + asset.reviewer_count
            )
            if difficulty != asset.difficulty:
                asset.difficulty = difficulty
                changed_assets.append(asset)

        if changed_assets:
            # We will only save the new difficulty score both for performance
            # and to avoid any possibility of race conditions causing stale data
            # to be saved:
            Asset.objects.bulk_update(changed_assets, ["difficulty"])
            updated_count += len(changed_assets)

    return updated_count


@celery_app.task
def populate_storage_image_values(asset_qs=None):
    """
    For Assets that existed prior to implementing the storage_image ImageField, build
    the relative S3 storage key for the asset and update the storage_image value
    """
    # As a reference point - how many records have a null storage image field?
    asset_storage_qs = Asset.objects.filter(
        storage_image__isnull=True
    ) | Asset.objects.filter(storage_image__exact="")
    storage_image_empty_count = asset_storage_qs.count()
    asset_qs = (
        Asset.objects.filter(storage_image__isnull=True)
        | Asset.objects.filter(storage_image__exact="")
        .order_by("id")
        .select_related("item__project__campaign")
        .only(
            "id",
            "storage_image",
            "media_url",
            "item",
            "item__item_id",
            "item__project",
            "item__project__slug",
            "item__project__campaign",
            "item__project__campaign__slug",
        )[:5000]
    )
    logger.debug("Total Storage image empty count %s" % storage_image_empty_count)
    logger.debug("Start storage image chunking")

    updated_count = 0

    # We'll process assets in chunks using an iterator to avoid saving objects
    # which will never be used again in memory. We will build the S3 relative key for
    # each existing asset and pass them to bulk_update() to be saved in a single query.
    for asset_chunk in chunked(asset_qs.iterator(), 1000):
        for asset in asset_chunk:
            asset.storage_image = "/".join(
                [
                    asset.item.project.campaign.slug,
                    asset.item.project.slug,
                    asset.item.item_id,
                    asset.media_url,
                ]
            )

        # We will only save the new storage image value both for performance
        # and to avoid any possibility of race conditions causing stale data
        # to be saved:

        Asset.objects.bulk_update(asset_chunk, ["storage_image"])
        updated_count += len(asset_chunk)

        logger.debug("Storage image updated count %s" % updated_count)

    return updated_count, storage_image_empty_count


@celery_app.task
def populate_asset_years():
    """
    Pull out date info from raw Item metadata and populate it for each Asset
    """

    asset_qs = Asset.objects.prefetch_related("item")

    updated_count = 0

    for asset_chunk in chunked(asset_qs, 500):
        changed_assets = []

        for asset in asset_chunk:
            metadata = asset.item.metadata

            year = None
            for date_outer in metadata["item"]["dates"]:
                for date_inner in date_outer.keys():
                    year = date_inner
                    break  # We don't support multiple values

            if asset.year != year:
                asset.year = year
                changed_assets.append(asset)

        if changed_assets:
            Asset.objects.bulk_update(changed_assets, ["year"])
            updated_count += len(changed_assets)

    return updated_count


@celery_app.task
def create_elasticsearch_indices():
    call_command("search_index", action="create")


@celery_app.task
def populate_elasticsearch_indices():
    call_command("search_index", action="populate")


@celery_app.task
def delete_elasticsearch_indices():
    call_command("search_index", "-f", action="delete")


@celery_app.task
def populate_user_archive_table():
    for campaign in Campaign.objects.filter(status=Campaign.Status.COMPLETED):
        assets = Asset.objects.filter(item__project__campaign=campaign)
        tag_collections = UserAssetTagCollection.objects.filter(asset__in=assets)
        user_campaigns = UserRetiredCampaign.objects.filter(campaign=campaign)

        to_update = user_campaigns.distinct()
        UserRetiredCampaign.objects.bulk_update(
            [
                UserRetiredCampaign(
                    id=user_profile_activity.id,
                    user_id=user_profile_activity.user.id,
                    campaign=campaign,
                    asset_count=assets.filter(
                        Q(transcription__user_id=user_profile_activity.user.id)
                        | Q(transcription__reviewed_by=user_profile_activity.user.id)
                    )
                    .distinct()
                    .count(),
                    asset_tag_count=Tag.objects.filter(
                        userassettagcollection__in=tag_collections.filter(
                            user_id=user_profile_activity.user.id
                        )
                    )
                    .distinct()
                    .count(),
                    transcribe_count=assets.filter(
                        transcription__user_id=user_profile_activity.user.id
                    )
                    .distinct()
                    .count(),
                    review_count=assets.filter(
                        transcription__reviewed_by=user_profile_activity.user.id
                    )
                    .distinct()
                    .count(),
                )
                for user_profile_activity in to_update
            ],
            ["asset_count", "asset_tag_count", "transcribe_count", "review_count"],
        )

        existing_user_campaigns = user_campaigns.values_list(
            "user_id", flat=True
        ).distinct()
        to_create = (
            User.objects.filter(transcription__asset__item__project__campaign=campaign)
            .exclude(id__in=existing_user_campaigns)
            .distinct()
            .values_list("id", flat=True)
        )
        UserRetiredCampaign.objects.bulk_create(
            [
                UserRetiredCampaign(
                    user_id=user_id,
                    campaign=campaign,
                    asset_count=assets.filter(
                        Q(transcription__user_id=user_id)
                        | Q(transcription__reviewed_by=user_id)
                    )
                    .distinct()
                    .count(),
                    asset_tag_count=Tag.objects.filter(
                        userassettagcollection__in=tag_collections.filter(
                            user_id=user_id
                        )
                    )
                    .distinct()
                    .count(),
                    transcribe_count=assets.filter(transcription__user_id=user_id)
                    .distinct()
                    .count(),
                    review_count=assets.filter(transcription__reviewed_by=user_id)
                    .distinct()
                    .count(),
                )
                for user_id in to_create
            ]
        )


@celery_app.task(ignore_result=True)
def retire_campaign(campaign_id):
    # Entry point to the retirement process
    campaign = Campaign.objects.get(id=campaign_id)
    logger.debug("Retiring %s (%s)" % (campaign, campaign.id))
    progress, created = CampaignRetirementProgress.objects.get_or_create(
        campaign=campaign
    )
    if created:
        # We want to set totals on a newly created progress object
        # but not on one that already exists. This allows us to keep proper
        # track of the full progress if the process is stopped and resumed
        projects = campaign.project_set.values_list("id", flat=True)
        items = Item.objects.filter(project__id__in=projects).values_list(
            "id", flat=True
        )
        assets = Asset.objects.filter(item__id__in=items).values_list("id", flat=True)
        progress.project_total = len(projects)
        progress.item_total = len(items)
        progress.asset_total = len(assets)
        progress.save()
    if campaign.status != Campaign.Status.RETIRED:
        logger.debug("Setting campaign status to retired")
        # We want to make sure the status is set to Retired before
        # we start removing information so the front-end is pulling
        # from archived data rather than live
        campaign.status = Campaign.Status.RETIRED
        campaign.save()
    remove_next_project.delay(campaign.id)
    return progress


@celery_app.task(ignore_result=True)
def project_removal_success(project_id, campaign_id):
    logger.debug(f"Updating progress for campaign {campaign_id}")
    logger.debug(f"Project id {project_id}")
    with transaction.atomic():
        progress = CampaignRetirementProgress.objects.select_for_update().get(
            campaign__id=campaign_id
        )
        progress.projects_removed = F("projects_removed") + 1
        progress.removal_log.append(
            {
                "type": "project",
                "id": project_id,
            }
        )
        progress.save()
        logger.debug(f"Progress updated for {campaign_id}")
    remove_next_project.delay(campaign_id)


@celery_app.task(ignore_result=True)
def remove_next_project(campaign_id):
    campaign = Campaign.objects.get(id=campaign_id)
    logger.debug("Removing projects for %s (%s)" % (campaign, campaign.id))
    try:
        project = campaign.project_set.all()[0]
        remove_next_item.delay(project.id)
    except IndexError:
        # This means all projects are deleted, which means the
        # campaign is fully retired.
        logger.debug(f"Updating progress for campaign {campaign_id}")
        logger.debug(f"Retirement complete for campaign {campaign_id}")
        with transaction.atomic():
            progress = CampaignRetirementProgress.objects.select_for_update().get(
                campaign__id=campaign_id
            )
            progress.complete = True
            progress.completed_on = timezone.now()
            progress.save()
        logger.debug(f"Progress updated for {campaign_id}")


@celery_app.task(ignore_result=True)
def item_removal_success(item_id, campaign_id, project_id):
    logger.debug(f"Updating progress for campaign {campaign_id}")
    logger.debug(f"Item id {item_id}")
    with transaction.atomic():
        progress = CampaignRetirementProgress.objects.select_for_update().get(
            campaign__id=campaign_id
        )
        progress.items_removed = F("items_removed") + 1
        progress.removal_log.append(
            {
                "type": "item",
                "id": item_id,
            }
        )
        progress.save()
    logger.debug(f"Progress updated for {campaign_id}")
    remove_next_item.delay(project_id)


@celery_app.task(ignore_result=True)
def remove_next_item(project_id):
    project = Project.objects.get(id=project_id)
    logger.debug("Removing items for %s (%s)" % (project, project.id))
    try:
        item = project.item_set.all()[0]
        remove_next_assets.delay(item.id)
    except IndexError:
        # No more items remain for this project, so we can now delete
        # the project
        logger.debug(f"All items remoed for {project} ({project.id})")
        campaign_id = project.campaign.id
        project_id = project.id
        project.delete()
        project_removal_success.delay(project_id, campaign_id)


@celery_app.task(ignore_result=True)
def assets_removal_success(asset_ids, campaign_id, item_id):
    logger.debug(f"Updating progress for campaign {campaign_id}")
    logger.debug(f"Asset ids {asset_ids}")
    with transaction.atomic():
        progress = CampaignRetirementProgress.objects.select_for_update().get(
            campaign__id=campaign_id
        )
        progress.assets_removed = F("assets_removed") + len(asset_ids)
        for asset_id in asset_ids:
            progress.removal_log.append(
                {
                    "type": "asset",
                    "id": asset_id,
                }
            )
        progress.save()
    logger.debug(f"Progress updated for {campaign_id}")
    remove_next_assets.delay(item_id)


@celery_app.task(ignore_result=True)
def remove_next_assets(item_id):
    item = Item.objects.get(id=item_id)
    campaign_id = item.project.campaign.id
    logger.debug("Removing assets for %s (%s)" % (item, item.id))
    assets = item.asset_set.all()
    if not assets:
        # No assets remain for this item, so we can safely delete it
        logger.debug(f"All assets removed for {item} ({item.id})")
        item_id = item.id
        project_id = item.project.id
        item.delete()
        item_removal_success.delay(item_id, campaign_id, project_id)
    else:
        # We delete assets in chunks of 10 in order to not lock up the database
        # for a long period of time.
        chord(delete_asset.s(asset.id) for asset in assets[:10])(
            assets_removal_success.s(campaign_id, item.id)
        )


@celery_app.task
def delete_asset(asset_id):
    asset = Asset.objects.get(id=asset_id)
    asset_id = asset.id
    logger.debug("Deleting asset %s (%s)" % (asset, asset_id))
    # We explicitly delete the storage image, though
    # this should be removed anyway when the asset is deleted
    asset.storage_image.delete(save=False)
    asset.delete()
    logger.debug("Asset %s (%s) deleted" % (asset, asset_id))

    return asset_id
