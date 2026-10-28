'use strict';
'require view';
'require fs';
'require dom';
'require ui';

function execCommand(path, args, failure) {
    return fs.exec(path, args).then(function(res) {
        if (res.code !== 0)
            throw new Error((res.stderr || res.stdout || failure || 'command failed').trim());
        return (res.stdout || '').trim();
    });
}

function command(args) {
    return execCommand('/usr/bin/r3s-policy', args, 'r3s-policy failed');
}

// Page loads use the read-only wrapper so a LuCI user with read access only
// can view everything but change nothing (see acl.d).
function readCommand(args) {
    return execCommand('/usr/bin/r3s-policy-ro', args, 'r3s-policy-ro failed');
}

function splitLines(text) {
    if (!text)
        return [];
    return text.split(/\n/).filter(function(line) { return line.trim() !== ''; });
}

function policyRows(text) {
    return splitLines(text).map(function(line) {
        var fields = line.split(/\t/);
        return {
            id: fields[0] || '',
            name: fields[1] || '',
            type: fields[2] || '',
            service: fields[3] || '',
            domains: fields[4] || '',
            quota: fields[5] || '',
            days: fields[6] || '',
            start: fields[7] || '',
            end: fields[8] || '',
            enabled: fields[9] || '',
            override: fields[10] || '',
            devices: fields[11] || '',
            deviceIds: fields[12] || ''
        };
    });
}

function deviceRows(text) {
    return splitLines(text).map(function(line) {
        var fields = line.split(/\t/);
        return {
            id: fields[0] || '',
            name: fields[1] || '',
            group: fields[2] || '',
            enabled: fields[3] || ''
        };
    });
}

function evaluateRows(text) {
    return splitLines(text).map(function(line) {
        var fields = line.split(/\t/);
        return {
            policy: fields[0] || '',
            deviceId: fields[1] || '',
            device: fields[2] || '',
            decision: fields[3] || '',
            reason: fields[4] || ''
        };
    });
}

function unresolvedRows(text) {
    return splitLines(text).map(function(line) {
        var fields = line.split(/\t/);
        return {
            ip: fields[0] || '',
            mac: fields[1] || '',
            bytes: fields[2] || '0'
        };
    });
}

function mb(bytes) {
    var value = Number(bytes);
    if (!isFinite(value) || value <= 0)
        return bytes || '-';
    return '%.1f MB'.format(value / 1000000);
}

function minuteText(value) {
    var minute = Number(value);
    if (!isFinite(minute))
        return '-';
    var hour = Math.floor(minute / 60);
    var min = minute % 60;
    return '%02d:%02d'.format(hour, min);
}

function scheduleText(days, start, end) {
    if (!days || days === '-')
        return '-';
    var names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var label = days.split(',').map(function(day) {
        var index = Number(day);
        return names[index] || day;
    }).join(', ');
    return '%s %s-%s'.format(label, minuteText(start), minuteText(end));
}

function parseTime(value) {
    var match = String(value || '').match(/^(\d{1,2}):(\d{2})$/);
    if (!match)
        return null;
    var hour = Number(match[1]);
    var minute = Number(match[2]);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59)
        return null;
    return hour * 60 + minute;
}

return view.extend({
    load: function() {
        return Promise.all([
            L.resolveDefault(readCommand(['list']), ''),
            L.resolveDefault(readCommand(['devices']), ''),
            L.resolveDefault(readCommand(['evaluate']), ''),
            L.resolveDefault(readCommand(['unresolved']), ''),
            L.resolveDefault(readCommand(['exclusions']), ''),
            L.resolveDefault(readCommand(['groups']), 'Personal\nKids\nIoT')
        ]);
    },

    associateMac: function(mac) {
        var device = document.querySelector('#r3s-unresolved-device-' + mac.replace(/[^a-z0-9]/gi, ''));
        if (!device || !device.value) {
            ui.addNotification(null, E('p', {}, _('Select a device first.')), 'warning');
            return Promise.resolve();
        }
        if (!confirm(_('Approve %s as an alias for device %s?').format(mac, device.value)))
            return Promise.resolve();
        return command(['associate-mac', '--device', device.value, '--mac', mac]).then(L.bind(function() {
            ui.addNotification(null, E('p', {}, _('MAC assigned. New traffic will resolve to that device.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    forgetUnresolved: function(mac) {
        if (!confirm(_('Forget unresolved MAC %s? Its current IP observation will be removed; historical usage is retained and it will reappear if seen again.').format(mac)))
            return Promise.resolve();
        return command(['forget-unresolved', '--mac', mac]).then(L.bind(function(message) {
            ui.addNotification(null, E('p', {}, message || _('Unresolved MAC forgotten.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    saveExclusions: function() {
        var text = document.querySelector('#r3s-adguard-exclusions').value;
        return command(['exclusions', '--text', text]).then(L.bind(function(message) {
            ui.addNotification(null, E('p', {}, message || _('Exclusion list saved.')), 'info');
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    syncDevices: function() {
        return command(['sync-devices']).then(L.bind(function(message) {
            ui.addNotification(null, E('p', {}, message || _('Device registry synced.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    addDevice: function() {
        var name = document.querySelector('#r3s-device-name').value.trim();
        var group = document.querySelector('#r3s-device-group').value;
        var mac = document.querySelector('#r3s-device-mac').value.trim();
        if (!name || !mac) {
            ui.addNotification(null, E('p', {}, _('Device name and MAC address are required.')), 'warning');
            return Promise.resolve();
        }
        return command(['add-device', '--name', name, '--group', group, '--mac', mac]).then(L.bind(function(message) {
            ui.addNotification(null, E('p', {}, message || _('Device registered.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    deleteDevice: function() {
        var select = document.querySelector('#r3s-device-delete');
        if (!select || !select.value) {
            ui.addNotification(null, E('p', {}, _('Select a device to remove.')), 'warning');
            return Promise.resolve();
        }
        var name = select.options[select.selectedIndex].text;
        if (!confirm(_('Remove "%s" from the registry? Its historical usage and policies will be retained, but it will no longer be attributed or enforced.').format(name)))
            return Promise.resolve();
        return command(['delete-device', '--device', select.value]).then(L.bind(function(message) {
            ui.addNotification(null, E('p', {}, message || _('Device removed.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    refresh: function() {
        return this.load().then(L.bind(function(data) {
            var container = document.querySelector('#r3s-policy-root');
            if (container)
                dom.content(container, this.renderContent(data));
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    collectPolicyArgs: function(commandName) {
        var name = document.querySelector('#r3s-policy-name').value.trim();
        var type = document.querySelector('#r3s-policy-type').value;
        var service = document.querySelector('#r3s-policy-service').value;
        var domains = document.querySelector('#r3s-policy-domains').value.trim();
        var selected = Array.prototype.slice.call(
            document.querySelector('#r3s-policy-devices').selectedOptions
        ).map(function(option) { return option.value; });
        var args = [commandName, '--name', name, '--type', type, '--service', service];

        if (!name) {
            ui.addNotification(null, E('p', {}, _('Policy name is required.')), 'warning');
            return null;
        }

        if (!selected.length) {
            ui.addNotification(null, E('p', {}, _('Select at least one device.')), 'warning');
            return null;
        }

        if (service === 'custom') {
            if (!domains) {
                ui.addNotification(null, E('p', {}, _('Enter at least one domain for the custom service.')), 'warning');
                return null;
            }
            args.push('--domains', domains);
        }

        if (type === 'service_quota') {
            var quotaMb = Number(document.querySelector('#r3s-policy-quota').value);
            if (!isFinite(quotaMb) || quotaMb <= 0) {
                ui.addNotification(null, E('p', {}, _('Quota must be a positive MB value.')), 'warning');
                return null;
            }
            args.push('--quota', String(Math.round(quotaMb * 1000000)));
        }
        else {
            var checkedDays = Array.prototype.slice.call(
                document.querySelectorAll('input[name="r3s-policy-day"]:checked')
            ).map(function(input) { return input.value; });
            var start = parseTime(document.querySelector('#r3s-policy-start').value);
            var end = parseTime(document.querySelector('#r3s-policy-end').value);
            if (!checkedDays.length || start == null || end == null) {
                ui.addNotification(null, E('p', {}, _('Schedule needs days plus HH:MM start and end times.')), 'warning');
                return null;
            }
            args.push('--days', checkedDays.join(','), '--start', String(start), '--end', String(end));
        }

        args.push('--devices');
        args = args.concat(selected);
        return args;
    },

    addPolicy: function() {
        var args = this.collectPolicyArgs('add');
        if (!args)
            return Promise.resolve();

        return command(args).then(L.bind(function() {
            ui.addNotification(null, E('p', {}, _('Policy added.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    beginPolicyEdit: function(row) {
        document.querySelector('#r3s-policy-edit-id').value = row.id;
        document.querySelector('#r3s-policy-name').value = row.name;
        document.querySelector('#r3s-policy-type').value = row.type;
        document.querySelector('#r3s-policy-service').value = row.service || 'internet';
        document.querySelector('#r3s-policy-domains').value = row.domains === '-' ? '' : row.domains;
        document.querySelector('#r3s-policy-quota').value = row.quota === '-' ? '' : String(Math.round(Number(row.quota) / 1000000));
        document.querySelector('#r3s-policy-start').value = row.start === '-' ? '' : minuteText(row.start);
        document.querySelector('#r3s-policy-end').value = row.end === '-' ? '' : minuteText(row.end);
        Array.prototype.forEach.call(document.querySelectorAll('input[name="r3s-policy-day"]'), function(input) {
            input.checked = row.days !== '-' && row.days.split(',').indexOf(input.value) !== -1;
        });
        Array.prototype.forEach.call(document.querySelector('#r3s-policy-devices').options, function(option) {
            option.selected = row.deviceIds !== '-' && row.deviceIds.split(',').indexOf(option.value) !== -1;
        });
        document.querySelector('#r3s-policy-form-title').textContent = _('Edit Policy #%s').format(row.id);
        document.querySelector('#r3s-policy-submit').textContent = _('Save policy');
        document.querySelector('#r3s-policy-cancel').style.display = '';
        window.scrollTo(0, document.querySelector('#r3s-policy-form-title').offsetTop);
    },

    cancelPolicyEdit: function() {
        document.querySelector('#r3s-policy-edit-id').value = '';
        document.querySelector('#r3s-policy-form-title').textContent = _('Add Policy');
        document.querySelector('#r3s-policy-submit').textContent = _('Add policy');
        document.querySelector('#r3s-policy-cancel').style.display = 'none';
    },

    savePolicy: function() {
        var policyId = document.querySelector('#r3s-policy-edit-id').value;
        if (!policyId)
            return this.addPolicy();
        var args = this.collectPolicyArgs('edit');
        if (!args)
            return Promise.resolve();
        args.splice(1, 0, '--policy-id', policyId);
        return command(args).then(L.bind(function(message) {
            ui.addNotification(null, E('p', {}, message || _('Policy updated.')), 'info');
            this.cancelPolicyEdit();
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    deletePolicy: function(policyId, name) {
        if (!confirm(_('Delete policy "%s"? This also removes its stored quota state.').format(name)))
            return Promise.resolve();
        return command(['delete', '--policy-id', policyId]).then(L.bind(function() {
            ui.addNotification(null, E('p', {}, _('Policy deleted.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    setQuota: function(policyId, name, currentQuota) {
        var currentMb = Math.max(1, Math.round(Number(currentQuota) / 1000000));
        var value = window.prompt(_('New daily quota in MB for "%s":').format(name), String(currentMb));
        if (value === null)
            return Promise.resolve();
        var quotaMb = Number(value);
        if (!isFinite(quotaMb) || quotaMb <= 0 || Math.round(quotaMb) !== quotaMb) {
            ui.addNotification(null, E('p', {}, _('Quota must be a positive whole number of MB.')), 'warning');
            return Promise.resolve();
        }
        return command(['set-quota', '--policy-id', policyId, '--quota', String(quotaMb * 1000000)]).then(L.bind(function() {
            ui.addNotification(null, E('p', {}, _('Quota changed. The evaluator applies it within 10 seconds.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    resetQuota: function(policyId, name) {
        if (!confirm(_('Reset today\'s usage for "%s"? This starts a fresh measurement for every attached device; historical usage is retained.').format(name)))
            return Promise.resolve();
        return command(['reset-quota', '--policy-id', policyId]).then(L.bind(function() {
            ui.addNotification(null, E('p', {}, _('Today\'s quota usage reset. The evaluator releases matching blocks within 10 seconds.')), 'info');
            return this.refresh();
        }, this)).catch(function(err) {
            ui.addNotification(null, E('p', {}, err.message), 'danger');
        });
    },

    renderContent: function(data) {
        var policies = policyRows(data[0]);
        var devices = deviceRows(data[1]);
        var decisions = evaluateRows(data[2]);
        var unresolved = unresolvedRows(data[3]);
        var exclusions = data[4] || '';
        var groups = splitLines(data[5]);
        if (!groups.length)
            groups = ['Personal', 'Kids', 'IoT'];
        var policyTableRows = policies.length ? policies.map(function(row) {
            var actions = [];
            actions.push(E('button', {
                'class': 'cbi-button cbi-button-action',
                'click': ui.createHandlerFn(this, 'beginPolicyEdit', row)
            }, _('Edit')));
            actions.push(' ');
            if (row.type === 'service_quota') {
                actions.push(E('button', {
                    'class': 'cbi-button cbi-button-action',
                    'click': ui.createHandlerFn(this, 'setQuota', row.id, row.name, row.quota)
                }, _('Change quota')));
                actions.push(' ');
                actions.push(E('button', {
                    'class': 'cbi-button cbi-button-action',
                    'click': ui.createHandlerFn(this, 'resetQuota', row.id, row.name)
                }, _('Reset today')));
                actions.push(' ');
            }
            actions.push(E('button', {
                'class': 'cbi-button cbi-button-negative',
                'click': ui.createHandlerFn(this, 'deletePolicy', row.id, row.name)
            }, _('Delete')));
            return E('tr', { 'class': 'tr' }, [
                E('td', { 'class': 'td' }, row.id),
                E('td', { 'class': 'td' }, row.name),
                E('td', { 'class': 'td' }, row.type),
                E('td', { 'class': 'td' }, row.service === 'custom' && row.domains ? '%s (%s)'.format(row.service, row.domains) : row.service),
                E('td', { 'class': 'td' }, row.type === 'service_quota' ? mb(row.quota) : '-'),
                E('td', { 'class': 'td' }, row.type === 'schedule' ? scheduleText(row.days, row.start, row.end) : '-'),
                E('td', { 'class': 'td' }, row.enabled === '1' ? _('Yes') : _('No')),
                E('td', { 'class': 'td' }, row.devices),
                E('td', { 'class': 'td' }, actions)
            ]);
        }, this) : [E('tr', { 'class': 'tr' }, E('td', { 'class': 'td', 'colspan': 9 }, _('No policies configured')))];
        var decisionTableRows = decisions.length ? decisions.map(function(row) {
            return E('tr', { 'class': 'tr' }, [
                E('td', { 'class': 'td' }, row.policy),
                E('td', { 'class': 'td' }, '%s %s'.format(row.deviceId, row.device)),
                E('td', { 'class': 'td' }, row.decision),
                E('td', { 'class': 'td' }, row.reason)
            ]);
        }) : [E('tr', { 'class': 'tr' }, E('td', { 'class': 'td', 'colspan': 4 }, _('No current decisions')))];
        var unresolvedTableRows = unresolved.length ? unresolved.map(L.bind(function(row) {
            var selectId = 'r3s-unresolved-device-' + row.mac.replace(/[^a-z0-9]/gi, '');
            return E('tr', { 'class': 'tr' }, [
                E('td', { 'class': 'td' }, row.ip),
                E('td', { 'class': 'td' }, row.mac),
                E('td', { 'class': 'td' }, mb(row.bytes)),
                E('td', { 'class': 'td' }, [
                    E('select', { 'id': selectId, 'class': 'cbi-input-select' }, [
                        E('option', { 'value': '' }, _('Select device'))
                    ].concat(devices.map(function(device) {
                        return E('option', { 'value': device.id }, '%s - %s (%s)'.format(device.id, device.name, device.group));
                    }))),
                    ' ',
                    E('button', { 'class': 'cbi-button cbi-button-action', 'click': ui.createHandlerFn(this, 'associateMac', row.mac) }, _('Assign')),
                    ' ',
                    E('button', { 'class': 'cbi-button cbi-button-negative', 'click': ui.createHandlerFn(this, 'forgetUnresolved', row.mac) }, _('Forget'))
                ])
            ]);
        }, this)) : [E('tr', { 'class': 'tr' }, E('td', { 'class': 'td', 'colspan': 4 }, _('No unresolved MACs')))];

        return E('div', { 'class': 'cbi-map' }, [
            E('h2', {}, _('Traffic Quotas')),
            E('p', { 'class': 'cbi-section-descr' },
                _('Configure per-device quotas and schedules stored in the traffic accounting database.')),

            E('h3', {}, _('MAC Management')),
            E('h4', {}, _('Device Registry')),
            E('p', { 'class': 'cbi-section-descr' },
                _('Register a new device or sync a manually updated registry. Historical unresolved usage is preserved.')),
            E('div', { 'class': 'cbi-section' }, [
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-device-name' }, _('Device name')),
                    E('div', { 'class': 'cbi-value-field' }, E('input', { 'id': 'r3s-device-name', 'class': 'cbi-input-text', 'type': 'text', 'placeholder': 'Bedroom Tablet' }))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-device-group' }, _('Group')),
                    E('div', { 'class': 'cbi-value-field' }, E('select', { 'id': 'r3s-device-group', 'class': 'cbi-input-select' },
                        groups.map(function(group) { return E('option', { 'value': group }, group); })))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-device-mac' }, _('MAC address')),
                    E('div', { 'class': 'cbi-value-field' }, E('input', { 'id': 'r3s-device-mac', 'class': 'cbi-input-text', 'type': 'text', 'size': '18', 'maxlength': '17', 'placeholder': 'aa:bb:cc:dd:ee:ff' }))
                ]),
                E('div', { 'class': 'cbi-page-actions' }, [
                    E('button', { 'class': 'cbi-button cbi-button-action', 'click': ui.createHandlerFn(this, 'addDevice') }, _('Register device')),
                    ' ',
                    E('button', { 'class': 'cbi-button', 'click': ui.createHandlerFn(this, 'syncDevices') }, _('Sync device registry'))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-device-delete' }, _('Remove device')),
                    E('div', { 'class': 'cbi-value-field' }, [
                        E('select', { 'id': 'r3s-device-delete', 'class': 'cbi-input-select' }, [
                            E('option', { 'value': '' }, _('Select device'))
                        ].concat(devices.map(function(device) {
                            return E('option', { 'value': device.id }, '%s - %s (%s)'.format(device.id, device.name, device.group));
                        }))),
                        ' ',
                        E('button', { 'class': 'cbi-button cbi-button-negative', 'click': ui.createHandlerFn(this, 'deleteDevice') }, _('Delete device'))
                    ])
                ])
            ]),

            E('h4', {}, _('Unresolved MACs')),
            E('p', { 'class': 'cbi-section-descr' },
                _('These clients are not assigned a device. Confirm the identity before assigning a MAC alias. Forget removes only a stale current observation; historical usage is not rewritten.')),
            E('table', { 'class': 'table' }, [
                E('tr', { 'class': 'tr table-titles' }, [
                    E('th', { 'class': 'th' }, _('IP address')),
                    E('th', { 'class': 'th' }, _('MAC address')),
                    E('th', { 'class': 'th' }, _('Unresolved usage')),
                    E('th', { 'class': 'th' }, _('Actions'))
                ])
            ].concat(unresolvedTableRows)),

            E('h3', {}, _('Policy Management')),
            E('h4', {}, _('Configured Policies')),
            E('table', { 'class': 'table' }, [
                E('tr', { 'class': 'tr table-titles' }, [
                    E('th', { 'class': 'th' }, _('ID')),
                    E('th', { 'class': 'th' }, _('Name')),
                    E('th', { 'class': 'th' }, _('Type')),
                    E('th', { 'class': 'th' }, _('Service')),
                    E('th', { 'class': 'th' }, _('Quota')),
                    E('th', { 'class': 'th' }, _('Schedule')),
                    E('th', { 'class': 'th' }, _('Enabled')),
                    E('th', { 'class': 'th' }, _('Devices')),
                    E('th', { 'class': 'th' }, _('Actions'))
                ]),
            ].concat(policyTableRows)),

            E('h4', {}, _('Current Decisions')),
            E('table', { 'class': 'table' }, [
                E('tr', { 'class': 'tr table-titles' }, [
                    E('th', { 'class': 'th' }, _('Policy')),
                    E('th', { 'class': 'th' }, _('Device')),
                    E('th', { 'class': 'th' }, _('Decision')),
                    E('th', { 'class': 'th' }, _('Reason'))
                ]),
            ].concat(decisionTableRows)),

            E('h4', { 'id': 'r3s-policy-form-title' }, _('Add Policy')),
            E('div', { 'class': 'cbi-section' }, [
                E('input', { 'id': 'r3s-policy-edit-id', 'type': 'hidden', 'value': '' }),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-policy-name' }, _('Name')),
                    E('div', { 'class': 'cbi-value-field' }, E('input', { 'id': 'r3s-policy-name', 'class': 'cbi-input-text', 'type': 'text' }))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-policy-type' }, _('Type')),
                    E('div', { 'class': 'cbi-value-field' }, E('select', { 'id': 'r3s-policy-type', 'class': 'cbi-input-select' }, [
                        E('option', { 'value': 'service_quota' }, _('Service quota')),
                        E('option', { 'value': 'schedule' }, _('Schedule'))
                    ]))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-policy-service' }, _('Service')),
                    E('div', { 'class': 'cbi-value-field' }, E('select', { 'id': 'r3s-policy-service', 'class': 'cbi-input-select' }, [
                        E('option', { 'value': 'internet' }, _('Internet')),
                        E('option', { 'value': 'custom' }, _('Custom domain'))
                    ]))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-policy-domains' }, _('Custom domains')),
                    E('div', { 'class': 'cbi-value-field' }, E('input', { 'id': 'r3s-policy-domains', 'class': 'cbi-input-text', 'type': 'text', 'maxlength': '4096', 'placeholder': 'example.com, video.example.com' }))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-policy-quota' }, _('Quota MB')),
                    E('div', { 'class': 'cbi-value-field' }, E('input', { 'id': 'r3s-policy-quota', 'class': 'cbi-input-text', 'type': 'number', 'min': '1', 'step': '1', 'value': '100' }))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title' }, _('Schedule Days')),
                    E('div', { 'class': 'cbi-value-field' }, ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(function(day, index) {
                        return E('label', { 'style': 'display:inline-block;margin-right:1em' }, [
                            E('input', { 'type': 'checkbox', 'name': 'r3s-policy-day', 'value': String(index) }),
                            ' ',
                            _(day)
                        ]);
                    }))
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title' }, _('Schedule Time')),
                    E('div', { 'class': 'cbi-value-field' }, [
                        E('input', { 'id': 'r3s-policy-start', 'class': 'cbi-input-text', 'type': 'time', 'value': '21:30' }),
                        ' ',
                        E('input', { 'id': 'r3s-policy-end', 'class': 'cbi-input-text', 'type': 'time', 'value': '07:00' })
                    ])
                ]),
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-policy-devices' }, _('Devices')),
                    E('div', { 'class': 'cbi-value-field' }, E('select', { 'id': 'r3s-policy-devices', 'class': 'cbi-input-select', 'multiple': 'multiple', 'size': '8' },
                        devices.map(function(device) {
                            return E('option', { 'value': device.id }, '%s - %s (%s)'.format(device.id, device.name, device.group));
                        })))
                ]),
                E('div', { 'class': 'cbi-page-actions' }, [
                    E('button', { 'id': 'r3s-policy-submit', 'class': 'cbi-button cbi-button-action', 'click': ui.createHandlerFn(this, 'savePolicy') }, _('Add policy')),
                    ' ',
                    E('button', { 'id': 'r3s-policy-cancel', 'class': 'cbi-button', 'style': 'display:none', 'click': ui.createHandlerFn(this, 'cancelPolicyEdit') }, _('Cancel edit')),
                    ' ',
                    E('button', { 'class': 'cbi-button', 'click': ui.createHandlerFn(this, 'refresh') }, _('Refresh'))
                ])
            ]),

            E('h3', {}, _('AdGuard Export Exclusions')),
            E('p', { 'class': 'cbi-section-descr' },
                _('These case-sensitive exclusions apply to command-line and Google Drive AdGuard exports. Large query logs are exported outside LuCI to avoid browser timeouts.')),
            E('div', { 'class': 'cbi-section' }, [
                E('div', { 'class': 'cbi-value' }, [
                    E('label', { 'class': 'cbi-value-title', 'for': 'r3s-adguard-exclusions' }, _('Domain exclusions')),
                    E('div', { 'class': 'cbi-value-field' }, [
                        E('textarea', { 'id': 'r3s-adguard-exclusions', 'class': 'cbi-input-textarea', 'rows': '8', 'maxlength': '16384' }, exclusions),
                        E('p', { 'class': 'cbi-value-description' }, _('One case-sensitive string per line. Matching domains are excluded from this export.'))
                    ])
                ]),
                E('div', { 'class': 'cbi-page-actions' }, [
                    E('button', { 'class': 'cbi-button', 'click': ui.createHandlerFn(this, 'saveExclusions') }, _('Save exclusions'))
                ])
            ])
        ]);
    },

    render: function(data) {
        return E('div', { 'id': 'r3s-policy-root' }, this.renderContent(data));
    }
});
