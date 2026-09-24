/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { showPushNotification } from "@web_push_notify/notification_service";

// Preserve the existing approval action tag for custom addons. Approval alerts
// keep their fixed minute even when older callers pass sticky for pending levels.
registry.category("actions").add("dam_display_notification", (env, action) => {
    const params = action.params || {};
    showPushNotification(env.services.notification, env.services.action, {
        ...params, title: params.title || _t("Approval"), duration: 60000, sticky: false,
    });
    return params.next;
});
