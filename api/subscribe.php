<?php
/* Show alerts signup (email and/or text). Saves to onc-private/subscribers.csv and emails ONC. */
define('ONC_API', true);
require __DIR__ . '/_lib.php';

const SMS_CONSENT_TEXT = 'Yes, text me Outta Nowhere Cards show alerts at this number. About 1-4 msgs/week. Msg & data rates may apply. Reply STOP to cancel, HELP for help. Consent is not a condition of purchase.';
const SUB_HEADER = ['signed_up', 'first_name', 'email', 'phone', 'email_alerts', 'text_alerts', 'text_consent_wording', 'interests', 'signed_up_from', 'ip', 'browser'];
const INTERESTS = ['Football', 'Baseball', 'Basketball', 'Hockey', 'Pokemon', 'Other'];

$back = onc_back_url('/#alerts');
onc_guard_request($back);

if (onc_is_bot()) onc_respond(true, "You're on the list. We'll give you a heads-up before the next show.", $back);
if (onc_rate_limited('subscribe', 6)) onc_respond(false, 'Too many signups from this connection. Please try again in an hour.', $back, 429);

$first   = onc_line('first_name', 40);
$email   = onc_email('email');
$phone   = onc_phone('phone');
$smsOk   = !empty($_POST['sms_ok']);
$likes   = onc_pick('interests', INTERESTS);
$source  = preg_replace('/[^a-z0-9\-]/', '', strtolower(onc_line('source', 30))) ?: 'site';

if ($email === '!') onc_respond(false, "That email address doesn't look right. Check it and try again.", $back, 422, ['field' => 'email']);
if ($phone === '!') onc_respond(false, 'Enter a 10-digit US mobile number, or leave the phone box blank.', $back, 422, ['field' => 'phone']);
if ($email === '' && $phone === '') onc_respond(false, 'Enter your email, your mobile number, or both.', $back, 422, ['field' => 'email']);
if ($phone !== '' && !$smsOk) onc_respond(false, 'Check the box to OK text alerts, or clear the phone number to get email only.', $back, 422, ['field' => 'sms_ok']);

// Already on the list?
$knownEmail = false;
foreach (onc_csv_rows('subscribers.csv') as $r) {
  $sameEmail = $email !== '' && strcasecmp($r['email'] ?? '', $email) === 0;
  if ($sameEmail) $knownEmail = true;
  $samePhone = $phone !== '' && ($r['phone'] ?? '') === $phone;
  $emailCovered = $email === '' || $sameEmail;
  $phoneCovered = $phone === '' || $samePhone;
  if (($sameEmail || $samePhone) && $emailCovered && $phoneCovered) {
    onc_respond(true, "You're already on the list. We'll keep the alerts coming.", $back, 200, ['already' => true]);
  }
}

onc_rate_limited('subscribe', 6, true);
$now = date('Y-m-d H:i:s');
$saved = onc_csv_append('subscribers.csv', SUB_HEADER, [
  $now, $first, $email, $phone,
  $email !== '' ? 'yes' : 'no',
  $phone !== '' ? 'yes' : 'no',
  $phone !== '' ? SMS_CONSENT_TEXT : '',
  implode('; ', $likes), $source, onc_client_ip(),
  mb_substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 160),
]);
$total = count(onc_csv_rows('subscribers.csv'));

// Tell ONC
$body = "New show-alert signup on On-Cards.com\n\n"
      . "Name:       " . ($first ?: '(not given)') . "\n"
      . "Email:      " . ($email ?: '(none)') . "\n"
      . "Text:       " . ($phone ? "$phone (agreed to texts)" : '(none)') . "\n"
      . "Interests:  " . ($likes ? implode(', ', $likes) : '(none picked)') . "\n"
      . "Signed up:  $now CT from the $source form\n\n"
      . ($saved ? "Total on the list: $total\n" : "WARNING: the website could not save this to the list file. Keep this email.\n")
      . "The full list is subscribers.csv in the onc-private folder (Hostinger File Manager, one level above public_html).\n";
$notified = onc_mail(ONC_NOTIFY_TO, 'New show-alert signup: ' . ($first ?: ($email ?: $phone)), $body, $email);

// Welcome email to the subscriber (once)
if ($email !== '' && !$knownEmail) {
  $hi = $first !== '' ? "Hey $first," : 'Hey,';
  $welcome = "$hi\n\n"
           . "You're on the Outta Nowhere Cards show-alert list. We'll send a heads-up before we go live on Whatnot, "
           . "plus new break lineups and big hits.\n\n"
           . "Follow the shows:  https://www.whatnot.com/user/outta_nowhere_cards\n"
           . "Website:           " . ONC_SITE . "/\n"
           . "Facebook:          https://www.facebook.com/profile.php?id=61593196346193\n\n"
           . "Don't want these anymore? Reply with \"unsubscribe\" and we'll take you off the list.\n\n"
           . "- Outta Nowhere Cards\n";
  onc_mail($email, "You're on the list — Outta Nowhere Cards show alerts", $welcome);
}

onc_log("subscribe source=$source email=" . ($email ? 'y' : 'n') . ' text=' . ($phone ? 'y' : 'n') . " saved=" . ($saved ? 'y' : 'n') . " mailed=" . ($notified ? 'y' : 'n'));

if (!$saved && !$notified) {
  onc_respond(false, "Something went wrong on our end and your signup didn't go through. Please try again, or email info@on-cards.com.", $back, 500);
}
onc_respond(true, $email !== ''
  ? "You're on the list. Check your inbox for a welcome email (look in spam or promotions if it's not there)."
  : "You're on the list. We'll text you before the next show.", $back);
