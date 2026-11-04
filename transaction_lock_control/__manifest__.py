{
    'name': 'Transaction Lock Control',
    'version': '18.0.1.2.0',
    'category': 'Accounting/Accounting',
    'summary': 'Apply accounting lock dates and exceptions to configured transactions',
    'license': 'LGPL-3',
    'depends': ['account', 'sale_stock', 'purchase_stock', 'base_automation'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/lock_rule_views.xml',
    ],
    'installable': True,
    'application': False,
}
