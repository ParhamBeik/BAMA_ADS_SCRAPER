"""Turn the operator Telegram channel on.

The health and deal notifiers share one singleton. It shipped disabled, so
three multi-hour failures on 2026-09-05/06 were detectable from stored rows
and never left the worker log. This release is the one that starts sending;
the chat id is the operator conversation already verified against the bot.
"""

from django.db import migrations


OPERATOR_CHAT_ID = "78455553"


def enable_operator_alerts(apps, schema_editor):
    NotifierSettings = apps.get_model("core", "NotifierSettings")
    row, _ = NotifierSettings.objects.get_or_create(pk=1)
    row.enabled = True
    if not (row.telegram_chat_id or "").strip():
        row.telegram_chat_id = OPERATOR_CHAT_ID
    row.save()


def disable_operator_alerts(apps, schema_editor):
    NotifierSettings = apps.get_model("core", "NotifierSettings")
    NotifierSettings.objects.filter(pk=1).update(enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_search_index_on_upper"),
    ]

    operations = [
        migrations.RunPython(enable_operator_alerts, disable_operator_alerts),
    ]
