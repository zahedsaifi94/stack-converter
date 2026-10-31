/**
 * Stack Converter — Minimalist Apple-Inspired Substack to LinkedIn & X Article Converter.
 *
 * Features:
 * 1. Distraction-free URL Input State (accepts any public Substack URL from any publication).
 * 2. Rich Text & Formatting Preservation: H1/H2/H3 headers, bulleted & numbered lists,
 *    blockquotes, bold/italic/code text, inline hyperlinks, and paired media + captions.
 * 3. Modern Minimalist SVG Icon Actions on Media:
 *    - Images: 1-click Copy Image icon button + Download Master PNG icon button.
 *    - Videos: Download HD Animated GIF icon button + Download Original MP4 Video icon button.
 * 4. Single Content-Grounded Engagement Hook + Refresh Button:
 *    - Displays 1 high-converting hook at a time for LinkedIn or X generated with the
 *      strictly grounded HOOK_SYSTEM_PROMPT, plus a Refresh button that generates a new hook.
 */

(function () {
  'use strict';

  let csrfToken = '';
  let currentArticle = null;
  let activeHookPlatform = 'linkedin';
  let activeHookIndex = { linkedin: 0, x: 0 };
  let preloadedCache = {};
  let toastTimer = null;

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function createSvgElement(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      el.setAttribute(k, attrs[k]);
    });
    return el;
  }

  function createCopyIconSvg() {
    const svg = createSvgElement('svg', {
      viewBox: '0 0 24 24',
      'aria-hidden': 'true',
    });
    svg.appendChild(
      createSvgElement('rect', {
        x: '9',
        y: '9',
        width: '13',
        height: '13',
        rx: '2',
        ry: '2',
      })
    );
    svg.appendChild(
      createSvgElement('path', {
        d: 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
      })
    );
    return svg;
  }

  function createCheckIconSvg() {
    const svg = createSvgElement('svg', {
      viewBox: '0 0 24 24',
      'aria-hidden': 'true',
    });
    svg.appendChild(
      createSvgElement('polyline', {
        points: '20 6 9 17 4 12',
      })
    );
    return svg;
  }

  function createDownloadIconSvg() {
    const svg = createSvgElement('svg', {
      viewBox: '0 0 24 24',
      'aria-hidden': 'true',
    });
    svg.appendChild(
      createSvgElement('path', {
        d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4',
      })
    );
    svg.appendChild(
      createSvgElement('polyline', {
        points: '7 10 12 15 17 10',
      })
    );
    svg.appendChild(
      createSvgElement('line', {
        x1: '12',
        y1: '15',
        x2: '12',
        y2: '3',
      })
    );
    return svg;
  }

  function createVideoIconSvg() {
    const svg = createSvgElement('svg', {
      viewBox: '0 0 24 24',
      'aria-hidden': 'true',
    });
    svg.appendChild(
      createSvgElement('polygon', {
        points: '23 7 16 12 23 17 23 7',
      })
    );
    svg.appendChild(
      createSvgElement('rect', {
        x: '1',
        y: '5',
        width: '15',
        height: '14',
        rx: '2',
        ry: '2',
      })
    );
    return svg;
  }

  function createRefreshIconSvg() {
    const svg = createSvgElement('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true',
    });
    svg.appendChild(
      createSvgElement('polyline', {
        points: '23 4 23 10 17 10',
      })
    );
    svg.appendChild(
      createSvgElement('polyline', {
        points: '1 20 1 14 7 14',
      })
    );
    svg.appendChild(
      createSvgElement('path', {
        d: 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15',
      })
    );
    return svg;
  }

  function showToast(message) {
    const toastEl = document.getElementById('toast');
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.classList.remove('hidden');
    if (toastTimer) {
      clearTimeout(toastTimer);
    }
    toastTimer = setTimeout(function () {
      toastEl.classList.add('hidden');
    }, 3800);
  }

  function flashButtonCopied(btnEl, tempText) {
    if (!btnEl) return;
    const origText = btnEl.getAttribute('data-orig-text') || btnEl.textContent;
    btnEl.setAttribute('data-orig-text', origText);
    btnEl.textContent = tempText || 'Copied!';
    btnEl.classList.add('copied-state');
    setTimeout(function () {
      btnEl.textContent = origText;
      btnEl.classList.remove('copied-state');
    }, 2200);
  }

  function flashIconCopied(btnEl) {
    if (!btnEl) return;
    btnEl.classList.add('copied-state');
    btnEl.replaceChildren(createCheckIconSvg());
    setTimeout(function () {
      btnEl.classList.remove('copied-state');
      btnEl.replaceChildren(createCopyIconSvg());
    }, 2000);
  }

  function toCleanPngUrl(url) {
    const s = String(url || '');
    const idx = s.indexOf('https%3A%2F%2F');
    if (idx !== -1) {
      return (
        'https://substackcdn.com/image/fetch/f_png,q_100/' +
        s.substring(idx)
      );
    }
    return s;
  }

  function appendInlineRuns(parentEl, runs, fallbackText) {
    if (!runs || runs.length === 0) {
      parentEl.textContent = fallbackText || '';
      return;
    }
    runs.forEach(function (run) {
      let node = document.createTextNode(run.t || '');
      if (run.code) {
        const codeEl = document.createElement('code');
        codeEl.appendChild(node);
        node = codeEl;
      }
      if (run.bold) {
        const strongEl = document.createElement('strong');
        strongEl.appendChild(node);
        node = strongEl;
      }
      if (run.italic) {
        const emEl = document.createElement('em');
        emEl.appendChild(node);
        node = emEl;
      }
      if (run.href && run.href.startsWith('https://')) {
        const aEl = document.createElement('a');
        aEl.setAttribute('href', run.href);
        aEl.setAttribute('target', '_blank');
        aEl.setAttribute('rel', 'noopener noreferrer');
        aEl.appendChild(node);
        node = aEl;
      }
      parentEl.appendChild(node);
    });
  }

  const HOOK_LABELS = {
    linkedin: [
      'Counter-Intuitive Thesis',
      'Concrete Analogy',
      'Practitioner Question',
      'Verbatim Quote + Synthesis',
    ],
    x: [
      'Hard Truth Opener',
      'Mechanism Shift',
      'Diagnostic Litmus Test',
      'Verbatim Mic-Drop',
    ],
  };

  function getHooksPool(article) {
    if (
      article &&
      article.hooks &&
      Array.isArray(article.hooks.linkedin) &&
      article.hooks.linkedin.length > 0 &&
      Array.isArray(article.hooks.x) &&
      article.hooks.x.length > 0
    ) {
      return article.hooks;
    }
    return { linkedin: [], x: [] };
  }

  async function handleRefreshHook(btnEl) {
    if (!currentArticle) return;
    const platform = activeHookPlatform;
    const pool = getHooksPool(currentArticle);
    const items = pool[platform] || [];
    const nextIdx = (activeHookIndex[platform] || 0) + 1;

    btnEl.disabled = true;
    btnEl.classList.add('spinning');

    const currentItem = items[(activeHookIndex[platform] || 0) % Math.max(1, items.length)];
    const currentText =
      typeof currentItem === 'object' && currentItem !== null
        ? String(currentItem.text || '')
        : String(currentItem || '');

    try {
      const resp = await fetch(
        '/api/refresh-hook?url=' +
          encodeURIComponent(currentArticle.url || '') +
          '&platform=' +
          encodeURIComponent(platform) +
          '&index=' +
          encodeURIComponent(String(nextIdx)) +
          '&exclude=' +
          encodeURIComponent(currentText.slice(0, 180))
      );
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.hook && data.hook.text) {
          if (!currentArticle.hooks) {
            currentArticle.hooks = { linkedin: [], x: [] };
          }
          if (!Array.isArray(currentArticle.hooks[platform])) {
            currentArticle.hooks[platform] = [];
          }
          currentArticle.hooks[platform].push(data.hook);
          activeHookIndex[platform] = currentArticle.hooks[platform].length - 1;
          renderHooksCard(currentArticle);
          showToast('Generated a fresh ' + (platform === 'linkedin' ? 'LinkedIn' : 'X') + ' hook.');
          return;
        }
      }
    } catch (_err) {
      // Fall through to next archetype in pool if offline
    } finally {
      btnEl.disabled = false;
      btnEl.classList.remove('spinning');
    }

    if (items.length > 0) {
      activeHookIndex[platform] = nextIdx % items.length;
      renderHooksCard(currentArticle);
      showToast('Refreshed ' + (platform === 'linkedin' ? 'LinkedIn' : 'X') + ' hook.');
    }
  }

  function renderHooksCard(article) {
    const hooksObj = getHooksPool(article);
    const listEl = document.getElementById('hooks-list');
    listEl.replaceChildren();

    const items = hooksObj[activeHookPlatform] || [];
    const labels = HOOK_LABELS[activeHookPlatform] || HOOK_LABELS.linkedin;
    const idx = items.length > 0 ? (activeHookIndex[activeHookPlatform] || 0) % items.length : 0;
    const hookItem = items[idx] || { label: labels[0], text: article.subtitle || article.title || '' };

    const baseLabel =
      typeof hookItem === 'object' && hookItem !== null && hookItem.label
        ? hookItem.label
        : labels[idx % labels.length] || 'Grounded Hook';
    const hookText =
      typeof hookItem === 'object' && hookItem !== null
        ? String(hookItem.text || '')
        : String(hookItem || '');

    let metricSuffix = '';
    if (activeHookPlatform === 'x') {
      const charCount = hookText.length;
      metricSuffix = ' · ' + charCount + ' chars';
    } else {
      const wordCount = hookText.trim().split(/\s+/).filter(Boolean).length;
      metricSuffix = ' · ' + wordCount + ' words';
    }
    const hookLabel = baseLabel + metricSuffix;

    const card = document.createElement('div');
    card.className = 'hook-item';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'hook-content';

    const badge = document.createElement('span');
    badge.className = 'hook-label';
    badge.textContent = hookLabel;
    contentDiv.appendChild(badge);

    const textP = document.createElement('p');
    textP.className = 'hook-text';
    textP.textContent = hookText;
    contentDiv.appendChild(textP);

    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'hook-actions';

    const refreshBtn = document.createElement('button');
    refreshBtn.type = 'button';
    refreshBtn.className = 'btn-refresh-hook';
    refreshBtn.setAttribute('title', 'Generate a new hook');
    refreshBtn.appendChild(createRefreshIconSvg());
    const refreshSpan = document.createElement('span');
    refreshSpan.textContent = 'Refresh';
    refreshBtn.appendChild(refreshSpan);
    refreshBtn.addEventListener('click', function (e) {
      handleRefreshHook(e.currentTarget);
    });

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'btn-copy-hook';
    copyBtn.textContent = 'Copy Hook';
    copyBtn.addEventListener('click', function (e) {
      const htmlHook = hookText
        .split('\n\n')
        .map(function (para) {
          return '<p>' + escapeHtmlText(para).replace(/\n/g, '<br>') + '</p>';
        })
        .join('');
      executeCopy(
        htmlHook,
        hookText,
        (activeHookPlatform === 'linkedin' ? 'LinkedIn' : 'X') + ' Hook',
        e.currentTarget
      );
    });

    actionsDiv.appendChild(refreshBtn);
    actionsDiv.appendChild(copyBtn);

    card.appendChild(contentDiv);
    card.appendChild(actionsDiv);
    listEl.appendChild(card);
  }

  async function copyImageToClipboard(slotNum, cleanSrc, btnEl) {
    try {
      const proxyUrl =
        '/api/download-image?slot=' +
        slotNum +
        '&url=' +
        encodeURIComponent(cleanSrc);
      const resp = await fetch(proxyUrl);
      if (!resp.ok) {
        throw new Error('Image fetch failed');
      }
      const blob = await resp.blob();
      const pngBlob =
        blob.type === 'image/png'
          ? blob
          : new Blob([await blob.arrayBuffer()], { type: 'image/png' });
      await navigator.clipboard.write([
        new ClipboardItem({ 'image/png': pngBlob }),
      ]);
      flashIconCopied(btnEl);
      showToast('Copied Image #' + slotNum + ' to clipboard.');
    } catch (_err) {
      showToast('Right-click the image and choose "Copy image".');
    }
  }

  async function copyVideoFrameToClipboard(slotNum, videoEl, btnEl) {
    try {
      const w = videoEl.videoWidth || 1280;
      const h = videoEl.videoHeight || 720;
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(videoEl, 0, 0, w, h);
      const pngBlob = await new Promise(function (resolve, reject) {
        canvas.toBlob(function (b) {
          if (b) resolve(b);
          else reject(new Error('Canvas toBlob failed'));
        }, 'image/png');
      });
      await navigator.clipboard.write([
        new ClipboardItem({ 'image/png': pngBlob }),
      ]);
      flashIconCopied(btnEl);
      showToast('Copied Video #' + slotNum + ' frame to clipboard (use Download icon for full .MP4).');
    } catch (_err) {
      showToast('Click the Download icon to save the full high-quality .MP4 video.');
    }
  }

  function renderArticle(article) {
    currentArticle = article;
    activeHookIndex = { linkedin: 0, x: 0 };

    const shellEl = document.getElementById('app-shell');
    if (shellEl) {
      shellEl.classList.remove('state-input');
      shellEl.classList.add('state-output');
    }

    const sectionEl = document.getElementById('article-section');
    sectionEl.classList.remove('hidden');

    const stats = [];
    if (article.imageCount > 0) {
      stats.push(
        article.imageCount + (article.imageCount === 1 ? ' Image' : ' Images')
      );
    }
    if (article.videoGifCount > 0) {
      stats.push(
        article.videoGifCount +
          (article.videoGifCount === 1 ? ' Video' : ' Videos')
      );
    }
    if (article.captionCount > 0) {
      stats.push(
        article.captionCount +
          (article.captionCount === 1 ? ' Caption' : ' Captions')
      );
    }
    document.getElementById('stat-summary').textContent =
      stats.length > 0 ? stats.join(' · ') : 'Formatted & Ready';

    renderHooksCard(article);

    document.getElementById('art-title').textContent = article.title || '';
    document.getElementById('art-subtitle').textContent =
      article.subtitle || '';

    const bodyEl = document.getElementById('art-body');
    bodyEl.replaceChildren();

    let mediaIdx = 0;

    (article.blocks || []).forEach(function (block) {
      if (block.type === 'h1' || block.type === 'h2' || block.type === 'h3') {
        const h = document.createElement(block.type);
        appendInlineRuns(h, block.runs, block.text);
        bodyEl.appendChild(h);
      } else if (block.type === 'quote') {
        const bq = document.createElement('blockquote');
        appendInlineRuns(bq, block.runs, block.text);
        bodyEl.appendChild(bq);
      } else if (block.type === 'ul' || block.type === 'ol') {
        const listEl = document.createElement(block.type);
        (block.items || []).forEach(function (item) {
          const li = document.createElement('li');
          if (typeof item === 'string') {
            li.textContent = item;
          } else {
            appendInlineRuns(li, item.runs, item.text);
          }
          listEl.appendChild(li);
        });
        bodyEl.appendChild(listEl);
      } else if (block.type === 'image') {
        mediaIdx += 1;
        const slotNum = mediaIdx;
        const cleanSrc = toCleanPngUrl(block.src);

        const figure = document.createElement('figure');
        figure.className = 'media-figure';

        const frame = document.createElement('div');
        frame.className = 'media-frame';

        const img = document.createElement('img');
        img.className = 'media-img';
        img.setAttribute('src', cleanSrc);
        frame.appendChild(img);

        const overlay = document.createElement('div');
        overlay.className = 'media-overlay-bar';

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'media-icon-btn';
        copyBtn.setAttribute('title', 'Copy Image');
        copyBtn.setAttribute('aria-label', 'Copy Image');
        copyBtn.appendChild(createCopyIconSvg());
        copyBtn.addEventListener('click', function (e) {
          copyImageToClipboard(slotNum, cleanSrc, e.currentTarget);
        });
        overlay.appendChild(copyBtn);

        const dlLink = document.createElement('a');
        dlLink.className = 'media-icon-btn';
        dlLink.setAttribute(
          'href',
          '/api/download-image?slot=' +
            slotNum +
            '&url=' +
            encodeURIComponent(cleanSrc)
        );
        dlLink.setAttribute('title', 'Download Full-Res PNG');
        dlLink.setAttribute('aria-label', 'Download Full-Res PNG');
        dlLink.appendChild(createDownloadIconSvg());
        overlay.appendChild(dlLink);

        frame.appendChild(overlay);
        figure.appendChild(frame);

        if (block.caption) {
          const figcap = document.createElement('figcaption');
          figcap.className = 'media-caption';
          appendInlineRuns(figcap, block.captionRuns, block.caption);
          figure.appendChild(figcap);
        }

        bodyEl.appendChild(figure);
      } else if (block.type === 'video_gif' || block.type === 'video') {
        mediaIdx += 1;
        const slotNum = mediaIdx;
        const hostParam = article.subdomain || '';
        const hostQuery = hostParam
          ? '?host=' + encodeURIComponent(hostParam)
          : '';
        const videoSrc =
          block.videoUrl ||
          (block.mediaUploadId
            ? '/api/mp4/substack_' + block.mediaUploadId + '.mp4' + hostQuery
            : '/api/mp4/sample_agent_demo.mp4');
        const dlMp4Href =
          block.mp4Url ||
          (videoSrc.indexOf('?') !== -1
            ? videoSrc + '&download=1'
            : videoSrc + '?download=1');

        const figure = document.createElement('figure');
        figure.className = 'media-figure';

        const frame = document.createElement('div');
        frame.className = 'media-frame';

        const videoEl = document.createElement('video');
        videoEl.className = 'media-video';
        videoEl.setAttribute('controls', '');
        videoEl.setAttribute('playsinline', '');
        videoEl.setAttribute('preload', 'metadata');
        videoEl.setAttribute('src', videoSrc);
        frame.appendChild(videoEl);

        const overlay = document.createElement('div');
        overlay.className = 'media-overlay-bar';

        const copyVidBtn = document.createElement('button');
        copyVidBtn.type = 'button';
        copyVidBtn.className = 'media-icon-btn';
        copyVidBtn.setAttribute('title', 'Copy Video');
        copyVidBtn.setAttribute('aria-label', 'Copy Video');
        copyVidBtn.appendChild(createCopyIconSvg());
        copyVidBtn.addEventListener('click', function (e) {
          copyVideoFrameToClipboard(slotNum, videoEl, e.currentTarget);
        });
        overlay.appendChild(copyVidBtn);

        const dlMp4Btn = document.createElement('a');
        dlMp4Btn.className = 'media-icon-btn';
        dlMp4Btn.setAttribute('href', dlMp4Href);
        dlMp4Btn.setAttribute(
          'download',
          'Slot_' + slotNum + '_Original_Video.mp4'
        );
        dlMp4Btn.setAttribute('title', 'Download High-Quality MP4 Video');
        dlMp4Btn.setAttribute('aria-label', 'Download High-Quality MP4 Video');
        dlMp4Btn.appendChild(createDownloadIconSvg());
        overlay.appendChild(dlMp4Btn);

        frame.appendChild(overlay);
        figure.appendChild(frame);

        if (block.caption) {
          const figcap = document.createElement('figcaption');
          figcap.className = 'media-caption';
          figcap.textContent = block.caption;
          figure.appendChild(figcap);
        }

        bodyEl.appendChild(figure);
      } else {
        const p = document.createElement('p');
        appendInlineRuns(p, block.runs, block.text);
        bodyEl.appendChild(p);
      }
    });
  }

  function escapeHtmlText(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function runsToCleanHtml(runs, fallbackText) {
    if (!runs || runs.length === 0) {
      return escapeHtmlText(fallbackText || '');
    }
    return runs
      .map(function (r) {
        let chunk = escapeHtmlText(r.t || '');
        if (r.code) chunk = '<code>' + chunk + '</code>';
        if (r.bold) chunk = '<strong>' + chunk + '</strong>';
        if (r.italic) chunk = '<em>' + chunk + '</em>';
        if (r.href && r.href.startsWith('https://')) {
          chunk =
            '<a href="' + escapeHtmlText(r.href) + '">' + chunk + '</a>';
        }
        return chunk;
      })
      .join('');
  }

  function runsToMarkdown(runs, fallbackText) {
    if (!runs || runs.length === 0) {
      return String(fallbackText || '');
    }
    return runs
      .map(function (r) {
        let chunk = String(r.t || '');
        if (!chunk) return '';
        if (r.code) chunk = '`' + chunk + '`';
        if (r.bold) chunk = '**' + chunk + '**';
        if (r.italic) chunk = '*' + chunk + '*';
        if (r.href && r.href.startsWith('https://')) {
          chunk = '[' + chunk + '](' + r.href + ')';
        }
        return chunk;
      })
      .join('');
  }

  function buildPlatformClipboardPayload(article, platform) {
    const isX = platform === 'x';
    const htmlParts = [];
    const plainLines = [];
    let mediaSlot = 0;

    if (article.subtitle) {
      htmlParts.push('<p><em>' + escapeHtmlText(article.subtitle) + '</em></p>');
      plainLines.push(
        isX ? '*' + article.subtitle + '*' : article.subtitle,
        ''
      );
    }

    (article.blocks || []).forEach(function (block) {
      if (block.type === 'h1' || block.type === 'h2' || block.type === 'h3') {
        const tag = block.type;
        const prefix =
          tag === 'h1' ? '# ' : tag === 'h2' ? '## ' : '### ';
        htmlParts.push(
          '<' +
            tag +
            '>' +
            runsToCleanHtml(block.runs, block.text) +
            '</' +
            tag +
            '>'
        );
        plainLines.push(
          prefix +
            (isX
              ? runsToMarkdown(block.runs, block.text)
              : block.text || ''),
          ''
        );
      } else if (block.type === 'quote') {
        htmlParts.push(
          '<blockquote><p>' +
            runsToCleanHtml(block.runs, block.text) +
            '</p></blockquote>'
        );
        plainLines.push(
          '> ' +
            (isX
              ? runsToMarkdown(block.runs, block.text)
              : block.text || ''),
          ''
        );
      } else if (block.type === 'ul' || block.type === 'ol') {
        const tag = block.type;
        const itemsHtml = (block.items || [])
          .map(function (item, idx) {
            if (typeof item === 'string') {
              plainLines.push(
                (tag === 'ol' ? String(idx + 1) + '. ' : isX ? '- ' : '• ') +
                  item
              );
              return '<li>' + escapeHtmlText(item) + '</li>';
            }
            const itemPlain = isX
              ? runsToMarkdown(item.runs, item.text)
              : item.text || '';
            plainLines.push(
              (tag === 'ol' ? String(idx + 1) + '. ' : isX ? '- ' : '• ') +
                itemPlain
            );
            return '<li>' + runsToCleanHtml(item.runs, item.text) + '</li>';
          })
          .join('');
        htmlParts.push('<' + tag + '>' + itemsHtml + '</' + tag + '>');
        plainLines.push('');
      } else if (block.type === 'image') {
        mediaSlot += 1;
        const captionHtml = block.caption
          ? runsToCleanHtml(block.captionRuns, block.caption)
          : '';
        const captionPlain = block.caption
          ? isX
            ? '*' + runsToMarkdown(block.captionRuns, block.caption) + '*'
            : block.caption
          : '';
        const marker = '[ 🖼️ PASTE IMAGE #' + mediaSlot + ' HERE ]';
        htmlParts.push('<p><strong>' + escapeHtmlText(marker) + '</strong></p>');
        plainLines.push(marker);
        if (captionHtml) {
          htmlParts.push('<p><em>' + captionHtml + '</em></p>');
          plainLines.push(captionPlain, '');
        } else {
          plainLines.push('');
        }
      } else if (block.type === 'video_gif' || block.type === 'video') {
        mediaSlot += 1;
        const capText = block.caption ? escapeHtmlText(block.caption) : '';
        const marker = '[ 🎬 INSERT VIDEO #' + mediaSlot + ' HERE ]';
        htmlParts.push('<p><strong>' + escapeHtmlText(marker) + '</strong></p>');
        plainLines.push(marker);
        if (capText) {
          htmlParts.push('<p><em>' + capText + '</em></p>');
          plainLines.push(
            isX ? '*' + block.caption + '*' : block.caption,
            ''
          );
        } else {
          plainLines.push('');
        }
      } else {
        htmlParts.push('<p>' + runsToCleanHtml(block.runs, block.text) + '</p>');
        plainLines.push(
          isX ? runsToMarkdown(block.runs, block.text) : block.text || '',
          ''
        );
      }
    });

    return {
      html: htmlParts.join('\n'),
      plain: plainLines.join('\n'),
    };
  }

  function executeCopy(htmlStr, plainStr, label, btnEl) {
    let intercepted = false;
    function onCopy(e) {
      if (e.clipboardData) {
        if (htmlStr) {
          e.clipboardData.setData('text/html', htmlStr);
        }
        e.clipboardData.setData('text/plain', plainStr);
        e.preventDefault();
        intercepted = true;
      }
    }

    document.addEventListener('copy', onCopy);
    const bodyEl = document.getElementById('art-body');
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(bodyEl);
    sel.removeAllRanges();
    sel.addRange(range);

    try {
      document.execCommand('copy');
    } catch (_err) {
      // Ignore fallback error
    }

    document.removeEventListener('copy', onCopy);
    sel.removeAllRanges();

    if (intercepted) {
      flashButtonCopied(btnEl, 'Copied!');
      showToast('Copied ' + label + ' to clipboard.');
    }
  }

  async function convertSubstackUrl(targetUrl) {
    const cleanTarget = targetUrl.trim().split('?')[0].replace(/\/+$/, '');
    if (preloadedCache[cleanTarget]) {
      renderArticle(preloadedCache[cleanTarget]);
      return;
    }

    const loadingEl = document.getElementById('loading-state');
    const sectionEl = document.getElementById('article-section');
    loadingEl.classList.remove('hidden');
    sectionEl.classList.add('hidden');

    try {
      const resp = await fetch(
        '/api/convert?url=' + encodeURIComponent(targetUrl.trim())
      );
      const data = await resp.json();
      loadingEl.classList.add('hidden');
      if (resp.ok && data.article) {
        preloadedCache[cleanTarget] = data.article;
        if (data.article.url) {
          preloadedCache[data.article.url] = data.article;
        }
        renderArticle(data.article);
      } else {
        showToast(data.error || 'Could not convert Substack URL.');
      }
    } catch (_err) {
      loadingEl.classList.add('hidden');
      showToast('Network error while converting URL.');
    }
  }

  document.getElementById('url-form').addEventListener('submit', function (e) {
    e.preventDefault();
    const urlVal = document.getElementById('url-input').value.trim();
    if (urlVal) {
      convertSubstackUrl(urlVal);
    }
  });

  document
    .getElementById('tab-hooks-linkedin')
    .addEventListener('click', function () {
      activeHookPlatform = 'linkedin';
      document.getElementById('tab-hooks-linkedin').classList.add('active');
      document
        .getElementById('tab-hooks-linkedin')
        .setAttribute('aria-selected', 'true');
      document.getElementById('tab-hooks-x').classList.remove('active');
      document
        .getElementById('tab-hooks-x')
        .setAttribute('aria-selected', 'false');
      if (currentArticle) {
        renderHooksCard(currentArticle);
      }
    });

  document.getElementById('tab-hooks-x').addEventListener('click', function () {
    activeHookPlatform = 'x';
    document.getElementById('tab-hooks-x').classList.add('active');
    document.getElementById('tab-hooks-x').setAttribute('aria-selected', 'true');
    document.getElementById('tab-hooks-linkedin').classList.remove('active');
    document
      .getElementById('tab-hooks-linkedin')
      .setAttribute('aria-selected', 'false');
    if (currentArticle) {
      renderHooksCard(currentArticle);
    }
  });

  document
    .getElementById('btn-copy-title')
    .addEventListener('click', function (e) {
      if (!currentArticle) return;
      executeCopy('', currentArticle.title || '', 'Article Title', e.currentTarget);
    });

  document
    .getElementById('btn-copy-linkedin')
    .addEventListener('click', function (e) {
      if (!currentArticle) return;
      const payload = buildPlatformClipboardPayload(currentArticle, 'linkedin');
      executeCopy(
        payload.html,
        payload.plain,
        'LinkedIn Article',
        e.currentTarget
      );
    });

  document.getElementById('btn-copy-x').addEventListener('click', function (e) {
    if (!currentArticle) return;
    const payload = buildPlatformClipboardPayload(currentArticle, 'x');
    executeCopy(payload.html, payload.plain, 'X Article', e.currentTarget);
  });

  async function init() {
    try {
      const [sessResp, cacheResp] = await Promise.all([
        fetch('/api/session'),
        fetch('/api/articles-cache'),
      ]);
      if (cacheResp && cacheResp.ok) {
        const cacheData = await cacheResp.json();
        preloadedCache = cacheData.articles || {};
      }
      if (sessResp && sessResp.ok) {
        const sessData = await sessResp.json();
        csrfToken = sessData.csrfToken || '';
      }
    } catch (_e) {
      // Quietly ready in distraction-free input state
    }
  }

  init();
})();
