# Inspected NOvA forms

The seven forms were inspected on 2026-10-27 through authenticated
`E/create_entry?f=NAME` pages. [forms.json](forms.json) records exact names,
labels, help text, links, HTML control types, dropdown/radio choices, and HTML
defaults. Names and tags were also checked against recent entries through
the XML API; historical answers are not bundled.

| Form | Fields | Helper | Default tags |
| --- | ---: | --- | --- |
| `2026_Control-Room_Checklist` | 26 | `post_control_room_checklist.py` | Control Room, General |
| `2026_Stable_Running_Checklist` | 42 | `post_stable_running_checklist.py` | Run Info |
| `2025_Expert-contact` | 5 | `post_expert_contact.py` | Expert |
| `2026_Start_Shift` | 19 | `post_start_shift.py` | Control Room, General |
| `2026_Shift-check-in` | 8 | `post_shift_check_in.py` | Control Room, General |
| `2026_Shift-check-out` | 3 | `post_shift_check_out.py` | Control Room |
| `2026_Shift-summary` | 10 | `post_shift_summary.py` | Control Room, General |

All observed examples used category `General`. A category is mandatory in the
web form. Lack of an HTML `required` attribute on another field does not prove
that the operational checklist considers it optional.

Use the helper's `--list-fields` to load only the selected form's schema.
Matching editable templates are in `assets/templates/FORM_NAME.json`.
Refresh with:

```bash
python3 "$SKILL/scripts/inspect_forms.py" \
  --web-credentials ../private/ecl_access_web.txt --output refreshed-forms.json
```

Inspect and reconcile changes with `references/forms.json`, the matching JSON
templates, and the helper's field options before replacing a snapshot. Run
`scripts/self_test.py` afterward. The inspector is read-only and keeps login
cookies in memory. Personal contact choices are removed by the inspector before
saving. Provide the current contact from verified user input; do not infer it
from a previous shift.

Significant details:

- Preserve `cr06_windows _present` (a space before the underscore),
  `fd_onmom_timestamp` (the form's spelling), and capitalized names such as
  `NOvA ECL`, `Shifter Name`, and `FD DAQ downtime`.
- Control Room checks cover FD/ND desktop timestamps, windows, rack temperature,
  spill server, disk watcher, cameras, dew points, and screenshots.
- Stable Running covers run numbers, trigger rates, alarms, event displays,
  monitoring timestamps, cooling, nearline plots, and attachments.
- Start Shift's help explicitly places it after Shift-check-in. It asks the
  shifter to check the bulletin board/ECL/AD Elog, contacts, runs, and beams.
- Expert-contact records a problem and the actual contact/response; its choices
  do not authorize this skill to contact anyone.
- Shift-check-in records shift identity, location, equipment, MCR check-in,
  and Zoom. Check-out records the next shifter and information transfer.
- Shift-summary records run summaries, beam/spills, nearline plots, and muon
  monitor observations.
- Many controls use HTML `multiple` selections. XML export of these fields
  is incomplete in this deployment; see [posting.md](posting.md).
