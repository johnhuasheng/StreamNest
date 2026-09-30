import assert from 'node:assert/strict';
import test from 'node:test';

import { maxVideoHeight, preferredFormatId, sortFormatsForDisplay, videoHeight } from '../app/quality.ts';

const formats = [
  { id: 'video-fast', label: '480P', kind: '快速' },
  { id: 'video-1', label: '1080P', kind: '高清' },
  { id: 'video-2', label: '720P', kind: '流畅' },
  { id: 'audio-1', label: 'MP3', kind: '音频' },
  { id: 'image-cover', label: '封面原图', kind: '图片' },
];

test('Bilibili defaults to the highest resolved video rather than quick 480P', () => {
  assert.equal(preferredFormatId('Bilibili', formats), 'video-1');
  assert.equal(maxVideoHeight(formats), 1080);
});

test('a real 2160P rendition is shown and selected as 4K', () => {
  const with4k = [
    ...formats,
    { id: 'video-4k', label: '2160P · 4K', kind: '超清' },
  ];
  assert.equal(preferredFormatId('Bilibili', with4k), 'video-4k');
  assert.equal(maxVideoHeight(with4k), 2160);
});

test('a core platform 4K label outranks 1080P without treating bitrate as resolution', () => {
  const coreFormats = [
    { id: 'core-1080', label: '1080P', kind: '高清' },
    { id: 'core-4k', label: '4K', kind: '高清' },
    { id: 'core-1200-kbps', label: '1200 kbps', kind: '快速' },
  ];
  assert.equal(videoHeight(coreFormats[1]), 2160);
  assert.equal(preferredFormatId('抖音', coreFormats), 'core-4k');
  assert.equal(maxVideoHeight(coreFormats), 2160);
});

test('all video platforms prefer the highest actually resolved quality', () => {
  for (const platform of ['YouTube', 'X', 'TED', '抖音', '快手', 'XVideos']) {
    assert.equal(preferredFormatId(platform, formats), 'video-1');
  }
  assert.equal(preferredFormatId('Bilibili', formats.slice(0, 1)), 'video-fast');
  assert.equal(maxVideoHeight(formats.slice(0, 1)), 480);
  assert.equal(preferredFormatId('Bilibili', []), '');
});

test('at the same resolution, select the higher quality stream instead of the fast muxed copy', () => {
  const withSameHeight = [
    { id: 'video-fast', label: '1080P', kind: '快速' },
    { id: 'video-quality', label: '1080P', kind: '高清' },
    { id: 'video-720', label: '720P', kind: '高清' },
  ];
  assert.equal(preferredFormatId('YouTube', withSameHeight), 'video-quality');
});

test('unnumbered TED bitrate choices keep the highest ranked first option', () => {
  const bitrateFormats = [
    { id: 'ted-1200', label: '1200 kbps', kind: '快速' },
    { id: 'ted-950', label: '950 kbps', kind: '高清' },
  ];
  assert.equal(preferredFormatId('TED', bitrateFormats), 'ted-1200');
});

test('image and audio formats are never mistaken for a higher video quality', () => {
  const media = [
    { id: 'image-1', label: '1080P', kind: '图片' },
    { id: 'audio-1', label: '2160P', kind: '音频' },
    { id: 'video-auto', label: '自动画质', kind: '快速' },
  ];
  assert.equal(preferredFormatId('TikTok', media), 'video-auto');
  assert.equal(maxVideoHeight(media), 0);
});

test('display order follows real source resolution, then audio and images', () => {
  const options = [
    { id: 'fast', label: '480P', kind: '快速' },
    { id: 'cover', label: '封面原图', kind: '图片' },
    { id: 'hd', label: '1080P · 60FPS', kind: '高清' },
    { id: 'fourk', label: '4K', kind: '超清' },
    { id: 'audio', label: 'MP3', kind: '音频' },
    { id: 'bitrate', label: '1200 kbps', kind: '快速' },
  ];
  assert.deepEqual(sortFormatsForDisplay(options).map((item) => item.id),
    ['fourk', 'hd', 'fast', 'bitrate', 'audio', 'cover']);
  assert.equal(videoHeight(options[2]), 1080);
  assert.equal(videoHeight(options[5]), 0);
  assert.deepEqual(options.map((item) => item.id),
    ['fast', 'cover', 'hd', 'fourk', 'audio', 'bitrate']);
});
