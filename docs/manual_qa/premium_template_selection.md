# Manual QA: Premium Template Selection

This checklist verifies that template selection behaves correctly for Telegram
accounts with and without Premium when sending test messages or preparing
campaigns.

## Prerequisites

1. Ensure at least two accounts exist in the database:
   - One account with `is_premium = True` (Premium enabled).
   - One account with `is_premium = False` (standard account).
2. Create two message templates:
   - **Standard template** using plain text only.
   - **Premium template** that includes at least one custom emoji placeholder
     (`[emoji:<id>]`) or a rich text span with a `emoji_id`.

## Testing the Testing Widget

1. Open the **Testing** widget.
2. Select the non-Premium account.
   - ✅ The template dropdown should hide the premium template and show a tooltip
     explaining why it is unavailable.
   - ✅ A dialog appears the first time the account is selected, explaining that
     Premium is required for the hidden templates.
3. Switch to the Premium account.
   - ✅ All templates, including the premium one, are visible and selectable.
4. Attempt to select the premium template, then switch back to the non-Premium
   account.
   - ✅ The selection resets to `None` and the message editor is cleared.
5. Try to send a test message using the premium template while the non-Premium
   account is selected (e.g., by reloading data so the template appears cached).
   - ✅ Sending is blocked with a warning explaining that the account lacks
     Telegram Premium.

## Campaigns and Bulk Sending

Repeat the above checks in any workflow that binds a template to a specific
account (e.g., campaign wizards or bulk send dialogs) to confirm premium-only
templates never queue for non-Premium accounts.
