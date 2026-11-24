<?php
/* "Sell us your cards" offers. Saves to onc-private/sell-offers.csv (+ photos in onc-private/sell-photos/<id>/)
   and emails ONC the details with the photos attached. */
define('ONC_API', true);
require __DIR__ . '/_lib.php';

const SELL_HEADER = ['received', 'offer_id', 'name', 'email', 'phone', 'location', 'selling', 'sports', 'how_many', 'asking', 'wants', 'details', 'heard_about_us', 'photos', 'status', 'ip'];
const WHAT   = ['Singles', 'Graded slabs', 'Sealed boxes/packs', 'Whole collection', 'Memorabilia', 'Other'];
const SPORTS = ['Football', 'Baseball', 'Basketball', 'Hockey', 'Soccer', 'Pokemon', 'Other TCG', 'Other'];
const COUNTS = ['1-10 cards', '11-50 cards', '51-250 cards', '250-1,000 cards', '1,000+ cards', 'Not sure'];
const WANTS  = ['Cash', 'Trade', 'Cash or trade'];
const MAX_PHOTOS = 10;
const MAX_PHOTO_BYTES = 15 * 1024 * 1024;      // per photo, as uploaded
const MAX_ATTACH_BYTES = 18 * 1024 * 1024;     // total photos attached to the notification email

$back = onc_back_url('/sell-your-cards/');

// Uploads bigger than the server's limit arrive with an empty form
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST' && empty($_POST) && (int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 0) {
  onc_respond(false, 'Those photos are too big to send in one go. Try fewer photos (or smaller ones) and send again.', $back, 413);
}
onc_guard_request($back);

if (onc_is_bot()) onc_respond(true, 'Got it. We\'ll look over your cards and get back to you by email.', $back);
if (onc_rate_limited('sell', 4)) onc_respond(false, 'Too many submissions from this connection. Please try again in an hour, or email info@on-cards.com.', $back, 429);

$name     = onc_line('name', 60);
$email    = onc_email('email');
$phone    = onc_phone('phone');
$location = onc_line('location', 60);
$what     = onc_pick('selling', WHAT);
$sports   = onc_pick('sports', SPORTS);
$count    = onc_choice('how_many', COUNTS, 'Not sure');
$asking   = onc_line('asking', 60);
$wants    = onc_choice('wants', WANTS, 'Cash or trade');
$details  = onc_text('details', 3000);
$heard    = onc_line('heard', 80);

$err = function (string $msg, string $field) use ($back) { onc_respond(false, $msg, $back, 422, ['field' => $field]); };
if ($name === '')                  $err('Enter your name.', 'name');
if ($email === '' || $email === '!') $err('Enter a working email so we can send you an offer.', 'email');
if ($phone === '!')                $err('Enter a 10-digit US phone number, or leave it blank.', 'phone');
if ($location === '')              $err('Enter your city or ZIP code.', 'location');
if (!$what)                        $err('Pick at least one thing you\'re selling.', 'selling');
if (mb_strlen($details) < 10)      $err('Tell us a little about the cards (players, years, sets, condition).', 'details');

/* ---------- photos ---------- */
$photos = [];   // ['name','type','data']
$skipped = 0;
$f = $_FILES['photos'] ?? null;
if ($f && is_array($f['name'])) {
  $finfo = new finfo(FILEINFO_MIME_TYPE);
  $n = count($f['name']);
  for ($i = 0; $i < $n; $i++) {
    if (($f['error'][$i] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) continue;
    if (count($photos) >= MAX_PHOTOS) { $skipped++; continue; }
    if ($f['error'][$i] !== UPLOAD_ERR_OK || $f['size'][$i] > MAX_PHOTO_BYTES || !is_uploaded_file($f['tmp_name'][$i])) { $skipped++; continue; }
    $type = $finfo->file($f['tmp_name'][$i]) ?: '';
    if (!in_array($type, ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif', 'image/gif'], true)) { $skipped++; continue; }
    $data = (string)file_get_contents($f['tmp_name'][$i]);
    $ext = 'jpg';
    // Re-save as a clean JPEG (max 2000px). This also strips hidden location data from phone photos.
    if (function_exists('imagecreatefromstring') && $type !== 'image/heic' && $type !== 'image/heif' && ($img = @imagecreatefromstring($data))) {
      $w = imagesx($img); $h = imagesy($img);
      $scale = min(1, 2000 / max($w, $h));
      if ($scale < 1) {
        $nw = max(1, (int)round($w * $scale)); $nh = max(1, (int)round($h * $scale));
        $dst = imagecreatetruecolor($nw, $nh);
        imagefill($dst, 0, 0, imagecolorallocate($dst, 255, 255, 255));
        imagecopyresampled($dst, $img, 0, 0, 0, 0, $nw, $nh, $w, $h);
        imagedestroy($img); $img = $dst;
      } elseif ($type === 'image/png' || $type === 'image/webp' || $type === 'image/gif') {
        $dst = imagecreatetruecolor($w, $h);
        imagefill($dst, 0, 0, imagecolorallocate($dst, 255, 255, 255));
        imagecopy($dst, $img, 0, 0, 0, 0, $w, $h);
        imagedestroy($img); $img = $dst;
      }
      ob_start(); imagejpeg($img, null, 85); $data = (string)ob_get_clean();
      imagedestroy($img);
      $type = 'image/jpeg';
    } else {
      $ext = ['image/jpeg' => 'jpg', 'image/png' => 'png', 'image/webp' => 'webp', 'image/heic' => 'heic', 'image/heif' => 'heif', 'image/gif' => 'gif'][$type] ?? 'bin';
    }
    $photos[] = ['name' => sprintf('photo-%02d.%s', count($photos) + 1, $ext), 'type' => $type, 'data' => $data];
  }
}

/* ---------- save ---------- */
onc_rate_limited('sell', 4, true);
$id = 'S' . date('ymd') . '-' . strtoupper(bin2hex(random_bytes(2)));
$now = date('Y-m-d H:i:s');
$dir = onc_private_dir();
$photoDir = '';
if ($photos && $dir !== '') {
  $photoDir = "$dir/sell-photos/$id";
  if (@mkdir($photoDir, 0750, true)) {
    foreach ($photos as $p) @file_put_contents("$photoDir/{$p['name']}", $p['data']);
  } else { $photoDir = ''; }
}
$saved = onc_csv_append('sell-offers.csv', SELL_HEADER, [
  $now, $id, $name, $email, $phone, $location, implode('; ', $what), implode('; ', $sports), $count, $asking, $wants,
  $details, $heard, count($photos), 'new', onc_client_ip(),
]);

/* ---------- email ONC (photos attached) ---------- */
$attach = []; $attBytes = 0; $notAttached = 0;
foreach ($photos as $p) {
  if ($attBytes + strlen($p['data']) > MAX_ATTACH_BYTES) { $notAttached++; continue; }
  $attach[] = $p; $attBytes += strlen($p['data']);
}
$body = "New \"Sell us your cards\" offer — $id\n\n"
      . "Name:       $name\n"
      . "Email:      $email   (just hit Reply to answer them)\n"
      . "Phone:      " . ($phone ?: '(not given)') . "\n"
      . "Location:   $location\n"
      . "Selling:    " . implode(', ', $what) . "\n"
      . "Sports:     " . ($sports ? implode(', ', $sports) : '(not picked)') . "\n"
      . "How many:   $count\n"
      . "Asking:     " . ($asking ?: '(no price given)') . "\n"
      . "Wants:      $wants\n"
      . "Heard of us:" . ($heard ? " $heard" : ' (not given)') . "\n\n"
      . "What they have:\n$details\n\n"
      . 'Photos:     ' . count($photos) . ' received'
      . ($notAttached ? ", $notAttached too big to attach (saved on the server)" : '')
      . ($skipped ? ", $skipped skipped (not a photo, or over 15 MB)" : '') . "\n"
      . ($photoDir ? "            Saved in onc-private/sell-photos/$id/\n" : '')
      . "\nReceived $now CT. " . ($saved ? 'Saved to onc-private/sell-offers.csv.' : 'WARNING: could not save to sell-offers.csv. Keep this email.') . "\n";
$notified = onc_mail(ONC_NOTIFY_TO, "Sell offer $id: " . implode(', ', $what) . " from $name ($location)", $body, $email, $attach);

/* ---------- confirmation to the seller ---------- */
$first = explode(' ', $name)[0];
$confirm = "Hey $first,\n\n"
         . "Thanks for reaching out to Outta Nowhere Cards about selling your cards. We got your submission"
         . ($photos ? ' and ' . count($photos) . ' photo' . (count($photos) === 1 ? '' : 's') : '') . ".\n\n"
         . "Reference: $id\n"
         . "Selling:   " . implode(', ', $what) . "\n"
         . "You want:  $wants\n\n"
         . "We'll look everything over and reply to this email with an offer or any questions. "
         . "If you have more photos or details, just reply to this email.\n\n"
         . "- Outta Nowhere Cards\n" . ONC_SITE . "/\n";
onc_mail($email, "We got your cards — Outta Nowhere Cards ($id)", $confirm);

onc_log("sell $id photos=" . count($photos) . " saved=" . ($saved ? 'y' : 'n') . " mailed=" . ($notified ? 'y' : 'n'));

if (!$saved && !$notified) {
  onc_respond(false, "Something went wrong on our end and your submission didn't go through. Please try again, or email info@on-cards.com.", $back, 500);
}
onc_respond(true, "Got it! Your reference is $id. We'll look over your cards and email you at $email with an offer or questions.", $back, 200, ['id' => $id]);
