/** @odoo-module **/

import { markup } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export function showPushNotification(notification, actions, payload, sanitizedHtml = false) {
    let timer;
    let close;
    const duration = Number.isInteger(payload.duration) && payload.duration >= 1000 &&
        payload.duration <= 86400000 ? payload.duration : 60000;
    const buttons = payload.action ? [{
        name: payload.action_label || _t("Open Document"), primary: true,
        onClick: async () => {
            await actions.doAction(payload.action);
            close();
        },
    }] : [];
    close = notification.add(sanitizedHtml ? markup(payload.message || "") : (payload.message || ""), {
        title: payload.title || _t("Notification"), type: payload.type || "info",
        sticky: true, buttons,
        onClose: () => browser.clearTimeout(timer),
    });
    // A fixed deadline, even while hovered. Never mark Discuss messages read.
    if (!payload.sticky) {
        timer = browser.setTimeout(close, duration);
    }
    return close;
}

registry.category("actions").add("web_push_notify.display", (env, action) => {
    // Client actions render plain text. Only our sanitized server bus uses HTML.
    showPushNotification(env.services.notification, env.services.action, action.params || {});
    return action.params?.next;
});

registry.category("services").add("webPushNotify", {
    dependencies: ["bus_service", "notification", "action"],
    start(env, { bus_service, notification, action }) {
        bus_service.subscribe("web_push_notify.notification", (payload) => {
            showPushNotification(notification, action, payload, true);
        });
        bus_service.start();
    },
});
