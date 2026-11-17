# SPDX-License-Identifier: GPL-3.0-or-later
"""Reminder times: the menu's presets, how a time is shown, and the pick-a-time dialog.

A reminder is one timestamp on a note (note.remind_at). When it comes, the app
pops the note out and sends a notification with Snooze and Done."""

import datetime

from gi.repository import Adw, GLib, Gtk

SNOOZE_MINUTES = 10
MORNING_HOUR = 9
EVENING_HOUR = 18


def _at(day, hour):
    return datetime.datetime.combine(day, datetime.time(hour))


def presets(now=None):
    """[(key, menu label, timestamp)] offered for a reminder, soonest first."""
    now = now or datetime.datetime.now()
    hour = (now + datetime.timedelta(hours=1)).replace(second=0, microsecond=0)
    out = [("hour", "In 1 hour", hour)]
    evening = _at(now.date(), EVENING_HOUR)
    if now < evening - datetime.timedelta(hours=1):
        out.append(("evening", f"This evening ({evening:%H:%M})", evening))
    tomorrow = _at(now.date() + datetime.timedelta(days=1), MORNING_HOUR)
    out.append(("tomorrow", f"Tomorrow morning ({tomorrow:%H:%M})", tomorrow))
    days = (7 - now.weekday()) % 7 or 7  # next Monday, never today
    monday = _at(now.date() + datetime.timedelta(days=days), MORNING_HOUR)
    out.append(("week", f"Next week ({monday:%a} {monday:%H:%M})", monday))
    return [(key, text, when.timestamp()) for key, text, when in out]


def preset_time(key, now=None):
    for name, _text, ts in presets(now):
        if name == key:
            return ts
    return None


def describe(ts, now=None):
    """Short form of a reminder time: "14:30", "Tomorrow 09:00", "Mon 23 Nov 09:00"."""
    now = now or datetime.datetime.now()
    when = datetime.datetime.fromtimestamp(ts)
    if when.date() == now.date():
        return f"{when:%H:%M}"
    if when.date() == now.date() + datetime.timedelta(days=1):
        return f"Tomorrow {when:%H:%M}"
    if when.year == now.year:
        return f"{when:%a} {when.day} {when:%b} {when:%H:%M}"
    return f"{when.day} {when:%b} {when.year} {when:%H:%M}"


class ReminderDialog(Adw.AlertDialog):
    """Pick a day and time; on_pick(timestamp) runs when one is set."""

    def __init__(self, current, on_pick):
        super().__init__(heading="Remind me", body="The note pops out at this time.")
        self._on_pick = on_pick
        start = datetime.datetime.fromtimestamp(current) if current else (
            datetime.datetime.now() + datetime.timedelta(hours=1))
        self.calendar = Gtk.Calendar()
        self.calendar.select_day(GLib.DateTime.new_local(start.year, start.month, start.day, 0, 0, 0))
        self.hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.minute = Gtk.SpinButton.new_with_range(0, 59, 5)
        self.hour.set_value(start.hour)
        self.minute.set_value(start.minute - start.minute % 5)
        for spin, name in ((self.hour, "Hour"), (self.minute, "Minute")):
            spin.set_orientation(Gtk.Orientation.VERTICAL)
            spin.set_wrap(True)
            spin.update_property([Gtk.AccessibleProperty.LABEL], [name])
            spin.connect("output", self._two_digits)
        clock = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        clock.append(self.hour)
        clock.append(Gtk.Label(label=":"))
        clock.append(self.minute)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.calendar)
        box.append(clock)
        self.set_extra_child(box)
        self.add_response("cancel", "Cancel")
        self.add_response("set", "Set Reminder")
        self.set_response_appearance("set", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("set")
        self.set_close_response("cancel")
        self.connect("response", self._on_response)

    @staticmethod
    def _two_digits(spin):
        spin.set_text(f"{int(spin.get_value()):02d}")
        return True

    def chosen(self):
        day = self.calendar.get_date()
        when = datetime.datetime(day.get_year(), day.get_month(), day.get_day_of_month(),
                                 int(self.hour.get_value()), int(self.minute.get_value()))
        return when.timestamp()

    def _on_response(self, _dialog, response):
        if response == "set":
            self._on_pick(self.chosen())
