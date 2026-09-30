chrome.runtime.onMessage.addListener((message) => {
  if (message?.source !== "streamnest-helper" || message.type !== "STREAMNEST_HELPER_PLAY") return;

  let attempts = 0;
  const timer = setInterval(() => {
    attempts += 1;
    const videos = [...document.querySelectorAll("video")]
      .filter((video) => video.readyState > 0 || video.currentSrc || video.src);
    const video = videos.sort((left, right) => (
      right.clientWidth * right.clientHeight - left.clientWidth * left.clientHeight
    ))[0];
    if (video) {
      video.muted = true;
      video.play().catch(() => undefined);
    }
    if (video?.currentSrc || attempts >= 40) clearInterval(timer);
  }, 500);
});
