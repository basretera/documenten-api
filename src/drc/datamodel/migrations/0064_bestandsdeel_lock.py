# Generated by Django 3.2.13 on 2023-10-19 12:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("datamodel", "0063_enkelvoudiginformatieobject_trefwoorden"),
    ]

    operations = [
        migrations.AddField(
            model_name="bestandsdeel",
            name="lock",
            field=models.CharField(
                blank=True,
                help_text="Hash string, which represents id of the lock of related informatieobject",
                max_length=255,
                null=True,
            ),
        ),
    ]