{
    'name': 'Web Push Notify',
    'version': '17.0.1.0.0',
    'summary': 'Reusable in-app popups and Discuss inbox notifications',
    'author': 'devkid',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['web', 'mail', 'bus'],
    'images': ['static/description/screenshot.png'],
    'assets': {
        'web.assets_backend': ['web_push_notify/static/src/notification_service.js'],
    },
    'application': True,
    'installable': True,
}
