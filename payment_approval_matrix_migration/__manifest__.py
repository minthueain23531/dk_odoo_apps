{
    'name': 'Payment Approval to Dynamic Approval Migration',
    'version': '18.0.6.0.0',
    'category': 'Administration',
    'summary': 'One-click approval migration with per-record retry and error logs',
    'license': 'LGPL-3',
    'depends': ['payment_approval_matrix', 'dynamic_approval_matrix'],
    'data': ['security/ir.model.access.csv', 'security/rules.xml', 'views/migration.xml', 'views/automatic.xml'],
    'installable': True,
    'application': False,
}
