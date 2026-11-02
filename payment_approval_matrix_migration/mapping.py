"""Pure mapping helpers. Legacy action strings are parsed, never executed."""
import ast
import re
import base64
import hashlib


class ArchiveOnly(Exception):
    """Legacy evidence is preserved, but does not establish an executable cycle."""


def signature_digest(value):
    if not value:
        return None
    try:
        return hashlib.sha1(base64.b64decode(value)).hexdigest()
    except (ValueError, TypeError):
        return None


def coalesce_levels(lines, approval_type):
    """Legacy allowed several alternative approver rows at the same level."""
    groups = {}
    for line in lines:
        level = line['level']
        bounds = (line['min_amount'], line['max_amount']) if approval_type == 'amount' else (0, 0)
        if level in groups and groups[level]['bounds'] != bounds:
            raise ValueError('Level %s has different amount ranges; an exact automatic merge is ambiguous.' % level)
        group = groups.setdefault(level, {'bounds': bounds, 'users': set()})
        group['users'].update(line['user_ids'])
    return [{'level': level, 'min_amount': groups[level]['bounds'][0],
             'max_amount': groups[level]['bounds'][1], 'user_ids': sorted(groups[level]['users'])}
            for level in sorted(groups)]


def decision_time_matches(message_date, legacy_created_at):
    """Compare at mail.message's seconds precision without changing stored dates.

    A same-second message can follow a microsecond-precision create_date. A
    message in an earlier second must still be rejected. Missing dates are not
    evidence of a decision.
    """
    if not message_date or not legacy_created_at:
        return False
    return message_date.replace(microsecond=0) >= legacy_created_at.replace(microsecond=0)


def legacy_status(status, levels):
    """Only explicit legacy status text establishes a cycle boundary."""
    if not levels:
        raise ValueError('No legacy evidence exists.')
    if status == 'Done':
        return 'done'
    rejected = re.fullmatch(r'Rejected By (\d+) \[ .* \]', status or '')
    pending = re.fullmatch(r'Level (\d+) Approved', status or '')
    match = rejected or pending
    if not match or int(match.group(1)) != max(levels):
        raise ValueError('Legacy status does not match the last evidence level.')
    return 'rejected' if rejected else 'pending'

# Only active Python integrations in the installed legacy addon are included.
MODEL_MAPPING = {
    'account.move': [('action_post', 'amount_total')],
    'account.payment': [('action_post', 'amount')],
    'hr.expense.sheet': [('action_approve_expense_sheets', 'total_amount')],
    'purchase.order': [('button_confirm', 'amount_total')],
    'payment.request': [('action_approve', 'amount_base_total')],
    'material.requisition': [('action_approve', '')],
    'material.inquiry': [('action_confirm', '')],
    'stock.inventory': [('action_validate', '')],
    'packing.lists': [('action_confirm', '')],
    'purchase.request': [('action_approve', 'grand_total')],
    'stock.return': [('action_approve', '')],
    'stock.sale.return': [('action_approve', '')],
    'sale.order': [('action_quotation_approve', 'amount_total'),
                   ('action_approve_sale_order', 'amount_total')],
    'stock.picking': [('button_validate', '')],
    'material.usage': [('action_confirm', '')],
    'res.partner': [('action_approve', '')],
}


def parse_legacy_action(text):
    """Accept exactly self.env['model'].browse(id).with_context(rollback=True).method()."""
    try:
        call = ast.parse(text or '', mode='eval').body
        if not isinstance(call, ast.Call) or call.args or call.keywords:
            return None
        method = call.func
        if not isinstance(method, ast.Attribute) or method.attr.startswith('_'):
            return None
        context = method.value
        if (not isinstance(context, ast.Call) or context.args
                or not isinstance(context.func, ast.Attribute)
                or context.func.attr != 'with_context' or len(context.keywords) != 1
                or context.keywords[0].arg != 'rollback'
                or not isinstance(context.keywords[0].value, ast.Constant)
                or context.keywords[0].value.value is not True):
            return None
        browse = context.func.value
        if (not isinstance(browse, ast.Call) or browse.keywords or len(browse.args) != 1
                or not isinstance(browse.func, ast.Attribute) or browse.func.attr != 'browse'
                or not isinstance(browse.args[0], ast.Constant)
                or type(browse.args[0].value) is not int or browse.args[0].value <= 0):
            return None
        sub = browse.func.value
        if (not isinstance(sub, ast.Subscript) or not isinstance(sub.slice, ast.Constant)
                or not isinstance(sub.slice.value, str) or not isinstance(sub.value, ast.Attribute)
                or sub.value.attr != 'env' or not isinstance(sub.value.value, ast.Name)
                or sub.value.value.id != 'self'):
            return None
        return sub.slice.value, browse.args[0].value, method.attr
    except (SyntaxError, ValueError, TypeError):
        return None


def validate_sequence(levels, decisions, expected, state):
    if not levels or len(set(levels)) != len(levels):
        raise ValueError('Missing evidence or repeated levels: split/review legacy cycles first.')
    if levels != expected[:len(levels)]:
        raise ValueError('Evidence must cover an uninterrupted prefix of applicable approval levels.')
    if any(value not in ('approved', 'rejected') for value in decisions):
        raise ValueError('Review every evidence decision before importing.')
    if state == 'rejected':
        if decisions != ['approved'] * (len(decisions) - 1) + ['rejected']:
            raise ValueError('A rejected cycle requires approved earlier levels and one final rejection.')
    elif any(value != 'approved' for value in decisions):
        raise ValueError('This cycle must contain approved evidence only.')
    if state == 'done' and levels != expected:
        raise ValueError('A completed cycle requires evidence for every applicable level.')
    if state == 'pending' and len(levels) >= len(expected):
        raise ValueError('Pending cycles require at least one remaining level.')
