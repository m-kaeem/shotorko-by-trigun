# Ward phone numbers: how to add them and how the SMS gets there

Every ward notice SMS goes to **one phone number per ward**, the ward officer's mobile. You add and change these numbers on the **Dispatch notices** page. You don't need to edit any files.

## Add a number (about 30 seconds per ward)

1. Open the dashboard and click **Dispatch notices** (or go to `http://localhost:8000/dispatch.html`).
2. In the **Wards** table, click the ward (for example *Ward 03 · Chattogram*).
3. In the panel on the right, under **Message to the ward officer**, make sure the **SMS** tab is selected.
4. In the **Who receives it** box, click **Add number**.
5. Type the officer's name (optional) and their mobile number, then click **Save number**.
   * Bangladeshi numbers can be typed any common way: `01712345678`, `01712-345678` or `+8801712345678`. They are saved as `+8801712345678`.
   * The app refuses a number that is too short or that isn't a Bangladeshi mobile (it must start with 013 to 019). Foreign numbers work if you start them with `+` and the country code.
6. The box now shows the name and a hidden number (`+8801••••••678`). A small phone icon appears next to the ward in the table. The line under the send button counts how many wards have their own number.

To **change** a number, click **Change**, type the new number and save. To **remove** it, click **Change**, then **Remove number**.

## How it works

```
 Dispatch page ──► Shotorko (this computer) ──► SMS outbox ──► Android phone with the SIM ──► ward officer
   you click Send     picks the number for          waits in line,     "SMS Gateway for Android"      receives
                      each ward (rules below)       retries if needed  sends from its own SIM         the SMS
```

**Who receives each ward's SMS**, checked in this order:

| The ward has… | The SMS goes to |
|---|---|
| its own number (saved on the Dispatch page) | **that number** |
| no number, but a demo number is set in `.env` (`SMS_OVERRIDE_TO`) | **the demo number**, so you can test without real officers |
| no number and no demo number | nobody: the ward is marked **No number** and the SMS is skipped |

The page always tells you which of these applies:
* The **Who receives it** box shows it for the open ward.
* The **SMS** column in the table says **No number** for wards that can't be reached.
* Before anything is sent, a confirmation lists how many messages go to ward numbers, how many go to the demo number and how many are skipped.

**Sending.** Click **Send SMS** for one ward, or **Send SMS to the other N** to send every ward that hasn't had one this week. The messages join an outbox, and the phone sends them one at a time with a short pause, because Android limits how fast an app may send SMS. The status in the table then moves from *queued* to *sending*, *sent* and *delivered*. If the phone is off or out of reach, messages wait in the outbox and go out when it's back. A failed message is retried up to 3 times. The same notice is never sent twice to the same number for the same week; **Send again** asks before re-sending.

**Practice mode.** When `SMS_MODE=dry_run` in `.env`, nothing is really sent. Messages are only logged, and the confirmation says so. Real sending needs `SMS_MODE=android` and the phone set up (cloud or local Wi-Fi route; see `.env.example`).

## Where the numbers are kept, and privacy

* Numbers are saved in `data/ward_contacts.csv` **on this computer only**. The file is created the first time you save a number.
* That file is listed in `.gitignore`, so **it is never committed or pushed to GitHub**. Each computer running Shotorko keeps its own list. To move the numbers to another computer, copy that one file across.
* The page never shows a full number again after you save it. It shows only the first digits and the last three (`+8801••••••678`), and so does the SMS history.
* The file has one row per ward: `ward_id, city, officer_name, phone`. You can also fill it in a spreadsheet: start from `data/ward_contacts.example.csv`, save it as `data/ward_contacts.csv`, and keep the column names.

## Before a live demo

1. Put **your own phone number** in `.env` as `SMS_OVERRIDE_TO`, so wards without a number text you instead of a stranger.
2. Add real officer numbers only for the wards you mean to reach.
3. Check the line under the send button (for example *"3 of 48 wards have their own number · the rest go to the demo number"*) before clicking **Send SMS to the other N**.
