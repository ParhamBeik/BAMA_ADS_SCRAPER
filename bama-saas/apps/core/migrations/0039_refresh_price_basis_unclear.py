"""Recompute the stored ``price_basis_unclear`` with the 2026-09-29 rule.

The column is derived on every observation, so rows only pick up a changed rule
when next crawled; removed ads never would. The old rule flagged the words for
financing, which the commonest cash-only dealer boilerplate contains, and hid
1,133 active cash listings. Frozen copy of ``quality.FINANCE`` — a migration must
not import code that will change after it.
"""

from django.db import migrations

FINANCE = (
    r"حواله|پیش.?پرداخت|پیش.?فروش|ثبت.?نام|مرحله.?ای"
    r"|تحویل.{0,3}[0-9۰-۹]+.{0,3}(روزه|ماهه|روز.?کاری)"
)
VERDICT = (
    "(price_type = 'installment' OR COALESCE(current_prepayment, 0) > 0 "
    "OR (COALESCE(title, '') || E'\\n' || COALESCE(description, '')) ~ %s)"
)


def refresh(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE catalog_ad SET price_basis_unclear = {VERDICT} "
            f"WHERE price_basis_unclear IS DISTINCT FROM {VERDICT}",
            [FINANCE, FINANCE],
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0038_ad_image_sweep")]
    operations = [migrations.RunPython(refresh, migrations.RunPython.noop)]
