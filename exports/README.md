# Sample dispatch notices

Generated offline by `python -m src.export_samples` for the week of 2025-07-07, 3-week horizon, regional stock 70%, 20 spray crews, wards at High risk or above (48 wards). Supply plan source: `engine`.

- `sms/`: one SMS per ward (English fits one 160-character message; Bangla is Unicode, 70 characters per part)
- `whatsapp/`: WhatsApp alerts with bold labels
- `briefs/`: printable office orders (open in a browser, then Print or Save as PDF)
- `dispatch_round_2025-07-07.csv`: the whole round in one table

Each ward has an English (`_en`) and a Bangla (`_bn`) version.
