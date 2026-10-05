// Run from the repository root: node web_push_notify/tests/test_notification_service.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const registrations = {};
const timers = new Map();
const messages = [];
let count = 0;
const browser = {
    setTimeout(fn, delay) { const id = ++count; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
};
const registry = { category(name) { return { add(key, value) { registrations[`${name}:${key}`] = value; } }; } };
const notification = { add(message, options) {
    const item = { message, options, closed: false };
    messages.push(item);
    return () => { if (!item.closed) { item.closed = true; options.onClose(); } };
} };
const actionsTaken = [];
const actions = { async doAction(action) { actionsTaken.push(action); } };
const context = { browser, registry, markup: value => value, _t: value => value };
vm.createContext(context);
const source = fs.readFileSync('web_push_notify/static/src/notification_service.js', 'utf8')
    .replace(/^import .*;\r?$/gm, '').replace('export function', 'function');
vm.runInContext(source, context);
(async () => {
    const close = context.showPushNotification(notification, actions, { message: 'Default' });
    assert.equal([...timers.values()][0].delay, 60000);
    [...timers.values()][0].fn();
    assert.equal(messages[0].closed, true);
    close();
    assert.equal(timers.size, 0);
    const manual = context.showPushNotification(notification, actions, { message: 'Custom', duration: 15000 });
    assert.equal([...timers.values()][0].delay, 15000);
    manual();
    assert.equal(timers.size, 0);
    context.showPushNotification(notification, actions, { message: 'Sticky', sticky: true });
    assert.equal(timers.size, 0);
    context.showPushNotification(notification, actions, { message: 'Open', action: { res_id: 7 } });
    await messages.at(-1).options.buttons[0].onClick();
    assert.equal(actionsTaken[0].res_id, 7);
    assert.equal(timers.size, 0);
    let listener;
    registrations['services:webPushNotify'].start({}, {
        bus_service: { subscribe(channel, fn) { assert.equal(channel, 'web_push_notify.notification'); listener = fn; }, start() {} },
        notification, action: actions,
    });
    listener({ message: 'Recipient', duration: 5000 });
    assert.equal(messages.at(-1).message, 'Recipient');
    assert.equal([...timers.values()][0].delay, 5000);
    const next = { type: 'ir.actions.act_window_close' };
    assert.equal(registrations['actions:web_push_notify.display']({ services: { notification, action: actions } },
        { params: { message: 'Current user', next } }), next);
    console.log('PASS: default/custom timeouts, sticky, manual close, document action, bus and client action.');
})().catch(error => { console.error(error); process.exitCode = 1; });
