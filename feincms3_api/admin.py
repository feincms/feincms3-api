from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _

from feincms3_api.models import APIToken


@admin.register(APIToken)
class APITokenAdmin(admin.ModelAdmin):
    """
    Every active staff user manages their own tokens, superusers everyone's

    A token never acts with more permissions than its user, so creating one
    needs no permission of its own. The secret is shown once, on a page of
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

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return ["user", *self.readonly_fields]
        return self.readonly_fields

    def get_list_display(self, request):
        if request.user.is_superuser:
            return self.list_display
        return [field for field in self.list_display if field != "user"]

    def _is_own(self, request, obj):
        return obj is None or request.user.is_superuser or obj.user == request.user

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and self._is_own(request, obj)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

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
