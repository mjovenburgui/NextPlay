"""Logros uno a uno: los de cada juego (Achievement), los que ha conseguido cada usuario y cuándo (UnlockedAchievement)
y cuándo se leyeron los de cada juego. Los de Steam que ya se habían leído, solo contados, se vuelven a leer en la
próxima sincronización para guardarlos uno a uno."""

import django.db.models.deletion
from django.db import migrations, models


def read_again(apps, schema_editor):
    """Marca para volver a leer los logros de Steam que se leyeron antes de guardarlos uno a uno."""
    ImportedTitle = apps.get_model("accounts", "ImportedTitle")
    ImportedTitle.objects.filter(title__platform="steam", achievements_total__gt=0).update(achievements_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_logros_de_los_titulos_importados'),
    ]

    operations = [
        migrations.AddField(
            model_name='externaltitle',
            name='achievements_read_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='logros del juego leídos'),
        ),
        migrations.CreateModel(
            name='Achievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('api_name', models.CharField(max_length=255, verbose_name='clave en la plataforma')),
                ('name', models.CharField(max_length=255, verbose_name='nombre')),
                ('description', models.CharField(blank=True, max_length=500, verbose_name='descripción')),
                ('icon', models.URLField(blank=True, max_length=300, verbose_name='icono')),
                ('icon_locked', models.URLField(blank=True, max_length=300, verbose_name='icono sin conseguir')),
                ('hidden', models.BooleanField(default=False, verbose_name='oculto')),
                ('percent', models.FloatField(blank=True, null=True, verbose_name='lo tiene (%)')),
                ('position', models.PositiveSmallIntegerField(default=0, verbose_name='orden')),
                ('title', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='achievements', to='accounts.externaltitle', verbose_name='título')),
            ],
            options={
                'verbose_name': 'logro',
                'verbose_name_plural': 'logros',
                'ordering': ['title', 'position', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='UnlockedAchievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('unlocked_at', models.DateTimeField(blank=True, null=True, verbose_name='conseguido')),
                ('achievement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='unlocks', to='accounts.achievement', verbose_name='logro')),
                ('imported', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='unlocked_achievements', to='accounts.importedtitle', verbose_name='título del usuario')),
            ],
            options={
                'verbose_name': 'logro conseguido',
                'verbose_name_plural': 'logros conseguidos',
                'ordering': [models.OrderBy(models.F('unlocked_at'), descending=True, nulls_last=True), '-pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='achievement',
            constraint=models.UniqueConstraint(fields=('title', 'api_name'), name='unique_achievement_per_title'),
        ),
        migrations.AddConstraint(
            model_name='unlockedachievement',
            constraint=models.UniqueConstraint(fields=('imported', 'achievement'), name='unique_unlocked_achievement'),
        ),
        migrations.RunPython(read_again, migrations.RunPython.noop),
    ]
