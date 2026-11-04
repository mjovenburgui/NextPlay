"""Avisos por tipo: lo que cada uno elige recibir en la sección «Avisos» de Editar perfil (Profile.notification_prefs).
El correo de las bajadas de precio, que era Profile.price_alert_emails, pasa a ser una elección más: quien lo tenía
apagado lo sigue teniendo apagado."""

from django.db import migrations, models


def forwards(apps, schema_editor):
    """Apaga en notification_prefs el correo de las bajadas de precio de quien lo tenía apagado en price_alert_emails."""
    Profile = apps.get_model("accounts", "Profile")
    Profile.objects.filter(price_alert_emails=False).update(notification_prefs={"precios": {"correo": False}})


def backwards(apps, schema_editor):
    """Al deshacerla, apaga price_alert_emails a quien tenía apagado el correo de las bajadas de precio."""
    Profile = apps.get_model("accounts", "Profile")
    for profile in Profile.objects.all():
        if ((profile.notification_prefs or {}).get("precios") or {}).get("correo") is False:
            profile.price_alert_emails = False
            profile.save(update_fields=["price_alert_emails"])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0021_logros_uno_a_uno'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='notification_prefs',
            field=models.JSONField(blank=True, default=dict, verbose_name='avisos que recibe'),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name='profile',
            name='price_alert_emails',
        ),
    ]
