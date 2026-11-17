import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class Fixture extends Extension {
    enable() {
        this._timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2800, () => {
            Main.overview.hide();
            this._timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
                this._timeout = 0;
                this._display();
                return GLib.SOURCE_REMOVE;
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    _display() {
        const here = this.dir.get_child('host-marker.txt');
        const [, pathBytes] = here.load_contents(null);
        const markerPath = new TextDecoder().decode(pathBytes).trim();
        const missingHome = !Gio.File.new_for_path(markerPath).query_exists(null);
        const missingSocket = !Gio.File.new_for_path('/run/dbus/system_bus_socket').query_exists(null);
        const noLeak = GLib.getenv('GNOME_AUDIT_SECRET') === null;
        const bus = Gio.DBus.system.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
            new GLib.Variant('(s)', ['org.freedesktop.login1']), null, Gio.DBusCallFlags.NONE, 750, null
        ).deepUnpack()[0];
        const [, routeBytes] = GLib.file_get_contents('/proc/net/route');
        const noRoute = new TextDecoder().decode(routeBytes).trim().split('\n').length === 1;
        const isolated = missingHome && missingSocket && noLeak && !bus && noRoute;

        this._card = new St.BoxLayout({
            vertical: true, x: 165, y: 140, width: 694, reactive: true,
            style: 'padding: 16px; spacing: 12px; background: #730e36; color: white; font: bold 26px Sans;',
        });
        this._card.add_child(new St.Label({text: isolated ? 'ISOLATION VERIFIED' : 'ISOLATION FAILED'}));
        this._entry = new St.Entry({
            can_focus: true, text: '',
            style: 'background: white; color: #202020; padding: 10px; font: bold 26px Sans;',
        });
        this._card.add_child(this._entry);
        this._counter = new St.Label({text: 'CLICK ZERO   SCROLL ZERO'});
        const button = new St.Button({
            label: 'CLICK TARGET', reactive: true, can_focus: true,
            style: 'background: #163fba; color: white; padding: 10px; font: bold 26px Sans;',
        });
        this._card.add_child(button);
        this._card.add_child(this._counter);
        let clicks = 0, scroll = 0;
        const word = count => ["ZERO", "ONE", "TWO"][count] ?? String(count);
        const update = () => this._counter.text = `CLICK ${word(clicks)}   SCROLL ${word(scroll)}`;
        button.connect('clicked', () => { ++clicks; update(); });
        this._card.connect('scroll-event', (_actor, event) => {
            if (event.get_scroll_direction() !== Clutter.ScrollDirection.SMOOTH) {
                ++scroll;
                update();
            }
            return Clutter.EVENT_STOP;
        });
        Main.layoutManager.addChrome(this._card);
        global.stage.set_key_focus(this._entry.clutter_text);
    }

    disable() {
        if (this._timeout) GLib.source_remove(this._timeout);
        this._timeout = 0;
        if (this._card) {
            Main.layoutManager.removeChrome(this._card);
            this._card.destroy();
            this._card = null;
        }
    }
}
