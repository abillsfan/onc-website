<?php
/* Show alerts signup (email). Saves to onc-private/subscribers.csv and emails ONC.
   Subscribers get an email when a Whatnot show is scheduled and when it goes live (api/show-alerts.php). */
define('ONC_API', true);
require __DIR__ . '/_lib.php';

// Same columns as before (phone/text columns stay for older rows; texts are no longer offered)
const SUB_HEADER = ['signed_up', 'first_name', 'email', 'phone', 'email_alerts', 'text_alerts', 'text_consent_wording', 'interests', 'signed_up_from', 'ip', 'browser'];
const INTERESTS = ['Football', 'Baseball', 'Basketball', 'Hockey', 'Pokemon', 'Other'];

$back = onc_back_url('/#alerts');
onc_guard_request($back);

if (onc_is_bot()) onc_respond(true, "You're on the list. We'll email you when the next show is scheduled.", $back);
if (onc_rate_limited('subscribe', 6)) onc_respond(false, 'Too many signups from this connection. Please try again in an hour.', $back, 429);

$first  = onc_line('first_name', 40);
$email  = onc_email('email');
$likes  = onc_pick('interests', INTERESTS);
$source = preg_replace('/[^a-z0-9\-]/', '', strtolower(onc_line('source', 30))) ?: 'site';

if ($email === '')  onc_respond(false, 'Enter your email address.', $back, 422, ['field' => 'email']);
if ($email === '!') onc_respond(false, "That email address doesn't look right. Check it and try again.", $back, 422, ['field' => 'email']);

// Already on the list? (Someone who unsubscribed and signs up again goes back on.)
$gone = onc_unsubscribed();
if (!isset($gone[$email])) {
  foreach (onc_csv_rows('subscribers.csv') as $r) {
    if (strcasecmp($r['email'] ?? '', $email) === 0 && ($r['email_alerts'] ?? '') === 'yes') {
      onc_respond(true, "You're already on the list. We'll keep the alerts coming.", $back, 200, ['already' => true]);
    }
  }
}

onc_rate_limited('subscribe', 6, true);
if (isset($gone[$email])) onc_resubscribe($email);
$now = date('Y-m-d H:i:s');
$saved = onc_csv_append('subscribers.csv', SUB_HEADER, [
  $now, $first, $email, '', 'yes', 'no', '', implode('; ', $likes), $source, onc_client_ip(),
  mb_substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 160),
]);
$total = count(onc_alert_recipients());

// Tell ONC
$body = "New show-alert signup on On-Cards.com\n\n"
      . "Name:       " . ($first ?: '(not given)') . "\n"
      . "Email:      $email\n"
      . "Interests:  " . ($likes ? implode(', ', $likes) : '(none picked)') . "\n"
      . "Signed up:  $now CT from the $source form\n\n"
      . ($saved ? "People on the list now: $total\n" : "WARNING: the website could not save this to the list file. Keep this email.\n")
      . "The list is subscribers.csv in the onc-private folder (Hostinger File Manager, one level above public_html).\n";
$notified = onc_mail(ONC_NOTIFY_TO, 'New show-alert signup: ' . ($first ?: $email), $body, $email);

// Welcome email
$hi = $first !== '' ? "Hey $first," : 'Hey,';
$welcome = "$hi\n\n"
         . "You're on the Outta Nowhere Cards show-alert list. You'll get an email when we put a new show on the "
         . "calendar, and another the second we go live on Whatnot.\n\n"
         . "Follow the shows:  https://www.whatnot.com/user/outta_nowhere_cards\n"
         . "Website:           " . ONC_SITE . "/\n"
         . "Facebook:          https://www.facebook.com/profile.php?id=61593196346193\n\n"
         . "- Outta Nowhere Cards"
         . onc_list_footer($email);
onc_mail($email, "You're on the list — Outta Nowhere Cards show alerts", $welcome, '', [], onc_list_headers($email));

onc_log("subscribe source=$source saved=" . ($saved ? 'y' : 'n') . " mailed=" . ($notified ? 'y' : 'n'));

if (!$saved && !$notified) {
  onc_respond(false, "Something went wrong on our end and your signup didn't go through. Please try again, or email info@on-cards.com.", $back, 500);
}
onc_respond(true, "You're on the list. Check your inbox for a welcome email (look in spam or promotions if it's not there).", $back);
