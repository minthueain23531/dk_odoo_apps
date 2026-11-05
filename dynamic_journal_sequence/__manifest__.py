{
    'name': 'Dynamic Journal Sequence',
    'version': '18.0.1.3.0',
    'category': 'Accounting/Accounting',
    'summary': 'Configure journal entry number formats per journal',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'views/seq_format_template.xml',
        'views/account_journal_views.xml',
        'views/move.xml',
    ],
    'installable': True,
    'application': False,
}
