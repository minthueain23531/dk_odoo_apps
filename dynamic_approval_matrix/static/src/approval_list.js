/** @odoo-module **/

import { useEffect, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ListRenderer } from "@web/views/list/list_renderer";

export function visibleApprovalRecords(list) {
    if (list.isGrouped) {
        return (list.groups || []).flatMap((group) =>
            group.isFolded ? [] : visibleApprovalRecords(group.list));
    }
    return (list.records || []).filter((record) => record.resId > 0);
}

patch(ListRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        this.damOrm = useService("orm");
        this.damStatus = useState({ enabled: false, values: {}, error: false });
        this.damSequence = 0;
        useEffect(() => {
            this.loadApprovalStatuses();
            return () => { this.damSequence++; };
        }, () => {
            const records = visibleApprovalRecords(this.props.list);
            return [this.props.list.resModel, records.map((record) => record.resId).join(","),
                ...records.map((record) => record.data)];
        });
    },

    async loadApprovalStatuses() {
        const model = this.props.list.resModel;
        if (this.isX2Many || (/^(ir|res|dam|base)\./.test(model) && !this.props.list.fields.dam_approval_status)) {
            return;
        }
        const sequence = ++this.damSequence;
        const ids = [...new Set(visibleApprovalRecords(this.props.list).map((record) => record.resId))];
        try {
            // Bound unusually large pages without issuing one RPC per record.
            const batches = [];
            for (let offset = 0; offset < Math.max(ids.length, 1); offset += 2000) {
                batches.push(this.damOrm.call("dam.rule", "get_list_status", [model, ids.slice(offset, offset + 2000)]));
            }
            const results = await Promise.all(batches);
            if (sequence === this.damSequence) {
                this.damStatus.enabled = results.some((result) => result.enabled);
                this.damStatus.values = Object.assign({}, ...results.map((result) => result.statuses));
                this.damStatus.error = false;
            }
        } catch {
            if (sequence === this.damSequence) {
                this.damStatus.values = {};
                this.damStatus.error = true;
            }
        }
    },

    processAllColumn(allColumns, list) {
        const columns = super.processAllColumn(...arguments);
        if (this.damStatus?.enabled) {
            columns.push({ id: "dam_approval_status", name: "dam_approval_status", type: "dam_status" });
        }
        return columns;
    },

    approvalListStatus(record) {
        return this.damStatus.error ? _t("Unavailable") :
            (this.damStatus.values[record.resId]?.text || _t("Not Started"));
    },
});
