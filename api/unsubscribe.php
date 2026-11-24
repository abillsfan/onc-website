<?php
/* One-click unsubscribe from show-alert emails.
   Link in every email: /api/unsubscribe.php?e=<email>&k=<token>
   GET shows a confirm button (so email scanners can't unsubscribe people by just opening the link);
   POST unsubscribes (also what Gmail/Apple Mail's built-in "Unsubscribe" button sends). */
define('ONC_API', true);
require __DIR__ . '/_lib.php';

$email = strtolower(trim((string)($_REQUEST['e'] ?? '')));
$key   = (string)($_REQUEST['k'] ?? '');
$valid = $email !== '' && filter_var($email, FILTER_VALIDATE_EMAIL) && hash_equals(onc_unsub_token($email), $key);

header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

function page(string $h1, string $msg, string $form = ''): void {
  echo "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
     . "<meta name=\"robots\" content=\"noindex\"><title>Show alerts | Outta Nowhere Cards</title>"
     . "<link rel=\"stylesheet\" href=\"/assets/style.css?v=20261123alerts\"></head>"
     . "<body><main class=\"final\" style=\"min-height:100vh;display:grid;place-items:center\"><div class=\"wrap narrow\" style=\"text-align:center\">"
     . "<h1 class=\"slash\">$h1</h1><p class=\"lede\">$msg</p>$form"
     . "<div class=\"btn-row center\"><a class=\"btn btn-blue btn-lg\" href=\"/\"><span>Back to On-Cards.com</span></a></div>"
     . "</div></main></body></html>";
  exit;
}

if (!$valid) {
  http_response_code(400);
  page('Link <span>expired.</span>', 'That unsubscribe link didn\'t work. Reply to any alert email with "unsubscribe", or email <a href="mailto:info@on-cards.com">info@on-cards.com</a>, and we\'ll take you off.');
}

$shown = htmlspecialchars($email, ENT_QUOTES);
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
  $oneClick = (string)($_POST['List-Unsubscribe'] ?? '') === 'One-Click';
  $ok = onc_unsubscribe($email, $oneClick ? 'mail app button' : 'unsubscribe page');
  onc_log("unsubscribe " . ($oneClick ? 'one-click' : 'page') . " ok=" . ($ok ? 'y' : 'n'));
  if ($oneClick) { echo 'ok'; exit; }
  page('You\'re <span>off the list.</span>', "We won't send show alerts to $shown anymore. Changed your mind? Sign up again on the homepage anytime.");
}

$e = htmlspecialchars($email, ENT_QUOTES); $k = htmlspecialchars($key, ENT_QUOTES);
page('Stop show <span>alerts?</span>', "Stop sending Outta Nowhere Cards show alerts to $shown?",
  "<form method=\"post\" class=\"btn-row center\" style=\"margin-top:20px\"><input type=\"hidden\" name=\"e\" value=\"$e\"><input type=\"hidden\" name=\"k\" value=\"$k\">"
  . "<button class=\"btn btn-whatnot btn-lg\" type=\"submit\" style=\"border:0;cursor:pointer\"><span>Yes, unsubscribe me</span></button></form>");
