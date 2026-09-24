/** @odoo-module **/

import { Component, useEffect, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class DynamicApprovalPanel extends Component {
    static template = "dynamic_approval_matrix.ApprovalPanel";
    static props = { record: Object, "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ panels: [], error: "" });
        this.sequence = 0;
        useEffect(() => {
            this.refresh();
            return () => { this.sequence++; };
        }, () => [this.props.record.resId, this.props.record.data]);
    }

    async refresh() {
        const sequence = ++this.sequence;
        const { resModel, resId } = this.props.record;
        if (!resId) {
            this.state.panels = [];
            return;
        }
        try {
            const panels = await this.orm.call("dam.rule", "get_panel", [resModel, resId]);
            if (sequence === this.sequence) {
                this.state.panels = panels;
                this.state.error = "";
            }
        } catch (error) {
            if (sequence === this.sequence) {
                this.state.error = _t("Approval status could not be loaded. Refresh the form or contact your administrator.");
            }
        }
    }

    async reset(panel) {
        const action = await this.orm.call("dam.request", "action_open_reset", [[panel.request_id]]);
        await this.action.doAction(action, { onClose: async () => {
            const record = this.props.record;
            if ("dam_approval_status" in record.fields && !(await record.isDirty())) {
                await record.load();
            }
            await this.refresh();
        } });
    }

    label(state) {
        return {
            not_started: _t("Not Started"), pending: _t("Pending"),
            approved: _t("Approved"), done: _t("Done"), superseded: _t("Document Changed"),
            rejected: _t("Rejected"), reset: _t("Not Started — Reset"),
        }[state] || state;
    }
}

registry.category("view_widgets").add("dam_approval_panel", { component: DynamicApprovalPanel });
