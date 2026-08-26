import re

from django.db import migrations, models

_SEP = re.compile(r"\s*[-–،,/]\s*")


def resplit_locations(apps, schema_editor):
    Ad = apps.get_model("core", "Ad")
    City = apps.get_model("core", "City")
    cache = {}
    batch = []
    for ad in Ad.objects.exclude(location="").iterator(chunk_size=1000):
        parts = [p.strip() for p in _SEP.split(ad.location.strip()) if p.strip()]
        if not parts:
            continue
        city_name, district = parts[0], " / ".join(parts[1:])
        if city_name not in cache:
            city, _ = City.objects.get_or_create(name_fa=city_name)
            cache[city_name] = city.pk
        ad.city_id = cache[city_name]
        ad.district = district
        batch.append(ad)
        if len(batch) >= 500:
            Ad.objects.bulk_update(batch, ["city", "district"], batch_size=500)
            batch = []
    if batch:
        Ad.objects.bulk_update(batch, ["city", "district"], batch_size=500)
    City.objects.filter(ads__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_alter_fetchrun_stop_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="ad",
            name="district",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.RunPython(resplit_locations, migrations.RunPython.noop),
    ]
