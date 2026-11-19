from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _

from feincms3_api.models import APIToken


@admin.register(APIToken)
class APITokenAdmin(admin.ModelAdmin):
    """
    Staff users manage their own tokens, superusers everyone's

    Each action needs its model permission, as everywhere in the admin
    (``feincms3_api.view_apitoken``, ``add_``, ``change_``, ``delete_``);
    grant them to whoever should have tokens. ``get_queryset()`` limits
    everyone but superusers to their own tokens. A token never acts with
    more permissions than its user. The secret is shown once, on a page of
    its own after saving.
    """

    list_display = [
        "name",
        "user",
        "access",
        "prefix",
        "created_at",
        "expires_at",
        "last_used_at",
    ]
    list_filter = ["access"]
    radio_fields = {"access": admin.HORIZONTAL}
    raw_id_fields = ["user"]
    search_fields = ["name", "prefix"]
    readonly_fields = ["prefix", "created_at", "last_used_at"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("user")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(user=request.user)

    def get_fields(self, request, obj=None):
        fields = ["name", "access", "expires_at"]
        if request.user.is_superuser or obj is not None:
            fields.insert(0, "user")
        if obj is not None:
            fields += self.readonly_fields
        return fields

    def get_changeform_initial_data(self, request):
        # Superusers may create tokens for others, but mostly for themselves
        return {"user": request.user.pk, **super().get_changeform_initial_data(request)}

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return ["user", *self.readonly_fields]
        return self.readonly_fields

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return [field for field in self.list_display if field != "user"]

    def save_model(self, request, obj, form, change):
        if not change:
            if not request.user.is_superuser or not obj.user_id:
                obj.user = request.user
            obj._secret = obj.set_secret()
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        if (secret := getattr(obj, "_secret", None)) is None:  # pragma: no cover
            return super().response_add(request, obj, post_url_continue)
        return TemplateResponse(
            request,
            "admin/feincms3_api/apitoken/created.html",
            {
                **self.admin_site.each_context(request),
                "title": _("API token created"),
                "opts": self.opts,
                "token": obj,
                "secret": secret,
            },
        )
