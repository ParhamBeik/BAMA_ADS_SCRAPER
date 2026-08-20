from django.db import migrations, models
import django.db.models.deletion


def assign_existing_favorites(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Favorite = apps.get_model("accounts", "Favorite")
    owner = User.objects.filter(is_staff=True).order_by("date_joined").first()
    if owner:
        Favorite.objects.filter(user__isnull=True).update(user=owner)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_add_is_demo"),
    ]

    operations = [
        migrations.AddField(
            model_name="favorite",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorites",
                to="accounts.user",
            ),
        ),
        migrations.AlterField(
            model_name="favorite",
            name="ad",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorites",
                to="core.ad",
            ),
        ),
        migrations.RunPython(assign_existing_favorites, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="favorite",
            constraint=models.UniqueConstraint(
                fields=("user", "ad"), name="uq_favorite_user_ad"
            ),
        ),
    ]
