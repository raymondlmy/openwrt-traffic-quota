'use strict';
'require view';
'require fs';
'require ui';
'require dom';

// Services > Telegram Reports. The token is written to a root-only drop file
// and applied by r3s-telegram-setup; it is never shown or passed as an argument.
var SETUP = '/usr/bin/r3s-telegram-setup';
var STATUS = '/usr/bin/r3s-telegram-status';
var PENDING = '/etc/r3s-accounting/secrets.new';

function run(path, args) {
    return fs.exec(path, args).then(function(res) {
        if (res.code !== 0)
            throw new Error((res.stderr || res.stdout || 'command failed').trim());
        return (res.stdout || '').trim();
    });
}

function notify(message, kind) {
    ui.addNotification(null, E('p', {}, message), kind || 'info');
}

function field(label, input, help) {
    return E('div', { 'class': 'cbi-value' }, [
        E('label', { 'class': 'cbi-value-title' }, label),
        E('div', { 'class': 'cbi-value-field' }, [ input, help ? E('div', { 'class': 'cbi-value-description' }, help) : '' ])
    ]);
}

return view.extend({
    load: function() {
        return run(STATUS, []).then(function(out) {
            return JSON.parse(out);
        }).catch(function(err) {
            return { missing: true, error: err.message };
        });
    },

    savePending: function(lines) {
        // 0600: the drop file holds the token until r3s-telegram-setup applies it.
        return fs.write(PENDING, lines.join('\n') + '\n', 384).then(function() {
            return run(SETUP, ['apply']);
        });
    },

    saveToken: function() {
        var token = document.querySelector('#r3s-tg-token').value.trim();
        if (!token)
            return notify(_('Paste the bot token from @BotFather first.'), 'warning');
        return this.savePending(['TELEGRAM_BOT_TOKEN=' + token]).then(L.bind(function(msg) {
            notify(msg || _('Token saved.'));
            return this.refresh();
        }, this)).catch(function(err) { notify(err.message, 'danger'); });
    },

    saveChat: function(chat) {
        chat = (chat || document.querySelector('#r3s-tg-chat').value).trim();
        if (!chat)
            return notify(_('Enter a chat ID or use Detect.'), 'warning');
        return this.savePending(['TELEGRAM_CHAT_ID=' + chat]).then(L.bind(function() {
            notify(_('Chat ID saved.'));
            return this.refresh();
        }, this)).catch(function(err) { notify(err.message, 'danger'); });
    },

    detectChat: function() {
        return run(SETUP, ['detect-chat']).then(L.bind(function(out) {
            var chats = JSON.parse(out || '[]');
            var box = document.querySelector('#r3s-tg-detected');
            if (!chats.length) {
                box.textContent = _('No chats found. Send any message to your bot in Telegram, then click Detect again.');
                return;
            }
            box.innerHTML = '';
            chats.forEach(L.bind(function(chat) {
                box.appendChild(E('div', {}, [
                    E('button', { 'class': 'cbi-button cbi-button-apply', 'click': ui.createHandlerFn(this, 'saveChat', chat.id) }, _('Use')),
                    ' ', chat.id, ' — ', chat.label
                ]));
            }, this));
        }, this)).catch(function(err) { notify(err.message, 'danger'); });
    },

    sendTest: function() {
        return run(SETUP, ['test']).then(function(msg) { notify(msg); })
            .catch(function(err) { notify(err.message, 'danger'); });
    },

    saveSchedule: function() {
        var args = ['schedule'];
        ['daily', 'daily-health', 'quota', 'health'].forEach(function(job) {
            args.push('--' + job, document.querySelector('#r3s-tg-job-' + job).checked ? 'on' : 'off');
        });
        var time = document.querySelector('#r3s-tg-time').value.trim();
        if (time)
            args.push('--daily-time', time);
        return run(SETUP, args).then(L.bind(function(msg) {
            notify(msg);
            return this.refresh();
        }, this)).catch(function(err) { notify(err.message, 'danger'); });
    },

    refresh: function() {
        return this.load().then(L.bind(function(status) {
            dom.content(document.querySelector('#r3s-tg-root'), this.renderBody(status));
        }, this));
    },

    renderBody: function(s) {
        if (s.missing)
            return E('div', { 'class': 'alert-message warning' },
                _('Telegram reports are not installed. Install the r3s-accounting-telegram package.'));

        var jobs = s.jobs || {};
        var labels = {
            'daily': _('Daily traffic report (yesterday, per device)'),
            'daily-health': _('Daily router health report'),
            'quota': _('Quota alerts (when a device uses up a quota)'),
            'health': _('Router CPU/memory alerts')
        };
        var state = s.token_set
            ? (s.bot ? _('Bot %s').format(s.bot) : _('Token saved, but Telegram could not be reached: %s').format(s.error || '?'))
            : _('No bot token yet');

        return E('div', {}, [
            E('div', { 'class': 'cbi-section' }, [
                E('h3', {}, _('1. Bot')),
                E('p', { 'class': 'cbi-section-descr' }, [
                    _('In Telegram, message @BotFather, send /newbot and copy the token it gives you. '),
                    E('strong', {}, state)
                ]),
                field(_('Bot token'),
                    E('input', { 'id': 'r3s-tg-token', 'class': 'cbi-input-password', 'type': 'password',
                                 'autocomplete': 'off', 'placeholder': s.token_set ? _('(saved — paste a new one to replace)') : '123456789:AAE…' }),
                    _('Checked with Telegram before saving. It is never shown again.')),
                E('button', { 'class': 'cbi-button cbi-button-save', 'click': ui.createHandlerFn(this, 'saveToken') }, _('Save token'))
            ]),
            E('div', { 'class': 'cbi-section' }, [
                E('h3', {}, _('2. Chat')),
                E('p', { 'class': 'cbi-section-descr' },
                    _('Send any message to your bot (or add it to a group and post there), then click Detect.')),
                field(_('Chat ID'),
                    E('input', { 'id': 'r3s-tg-chat', 'class': 'cbi-input-text', 'type': 'text', 'value': s.chat_id || '' })),
                E('button', { 'class': 'cbi-button', 'click': ui.createHandlerFn(this, 'detectChat'), 'disabled': s.token_set ? null : '' }, _('Detect')),
                ' ',
                E('button', { 'class': 'cbi-button cbi-button-save', 'click': ui.createHandlerFn(this, 'saveChat', null) }, _('Save chat ID')),
                ' ',
                E('button', { 'class': 'cbi-button', 'click': ui.createHandlerFn(this, 'sendTest'),
                              'disabled': (s.token_set && s.chat_id) ? null : '' }, _('Send test message')),
                E('div', { 'id': 'r3s-tg-detected', 'style': 'margin-top:.5em' })
            ]),
            E('div', { 'class': 'cbi-section' }, [
                E('h3', {}, _('3. Reports')),
                E('div', {}, Object.keys(labels).map(function(job) {
                    return E('div', {}, E('label', {}, [
                        E('input', { 'type': 'checkbox', 'id': 'r3s-tg-job-' + job, 'checked': jobs[job] ? '' : null }),
                        ' ', labels[job]
                    ]));
                })),
                field(_('Daily reports at'),
                    E('input', { 'id': 'r3s-tg-time', 'class': 'cbi-input-text', 'type': 'text', 'size': 5,
                                 'value': s.daily_time || '06:00', 'placeholder': 'HH:MM' }),
                    _('Router local time.')),
                E('button', { 'class': 'cbi-button cbi-button-save', 'click': ui.createHandlerFn(this, 'saveSchedule') }, _('Save reports'))
            ])
        ]);
    },

    render: function(status) {
        return E('div', { 'class': 'cbi-map' }, [
            E('h2', {}, _('Telegram Reports')),
            E('p', { 'class': 'cbi-section-descr' },
                _('Send daily traffic reports and quota alerts to a Telegram chat.')),
            E('div', { 'id': 'r3s-tg-root' }, this.renderBody(status))
        ]);
    },

    handleSave: null,
    handleSaveApply: null,
    handleReset: null
});
