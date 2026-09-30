/*! Background: soft aurora + drifting orbs — inspired by sahandm96.com identity canvas. */
(() => {
  "use strict";
  const canvas = document.getElementById("bgCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const low = window.matchMedia("(max-width: 720px)").matches
    || (navigator.deviceMemory && navigator.deviceMemory <= 4);

  let W = 0, H = 0, dpr = 1, raf = 0, t0 = 0;
  const orbs = [];
  const N = reduce ? 0 : (low ? 18 : 36);

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function spawn(o, seeded) {
    o.x = Math.random() * W;
    o.y = seeded ? Math.random() * H : -40 - Math.random() * H * 0.3;
    o.r = 6 + Math.random() * (low ? 28 : 48);
    o.vy = 8 + Math.random() * 28;
    o.vx = (Math.random() - 0.5) * 14;
    o.spin = Math.random() * Math.PI * 2;
    o.spinSp = (Math.random() - 0.5) * 0.6;
    o.a = 0.06 + Math.random() * 0.14;
    // teal / gold / soft violet (site spectrum, GameMap accent)
    const pal = [
      [46, 196, 166],
      [244, 185, 66],
      [139, 92, 246],
      [59, 130, 246],
      [56, 189, 248],
    ];
    o.c = pal[(Math.random() * pal.length) | 0];
  }

  for (let i = 0; i < N; i++) {
    const o = {};
    spawn(o, true);
    orbs.push(o);
  }

  function paint(now) {
    const t = (now - t0) / 1000;
    ctx.clearRect(0, 0, W, H);

    // drifting aurora blobs
    const g1 = ctx.createRadialGradient(
      W * (0.5 + Math.sin(t * 0.15) * 0.2), -H * 0.1,
      0, W * 0.5, H * 0.2, W * 0.7
    );
    g1.addColorStop(0, "rgba(139, 92, 246, 0.22)");
    g1.addColorStop(0.45, "rgba(59, 130, 246, 0.10)");
    g1.addColorStop(1, "transparent");
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    const g2 = ctx.createRadialGradient(
      W * (0.1 + Math.cos(t * 0.12) * 0.08), H * 0.25,
      0, 0, H * 0.3, W * 0.55
    );
    g2.addColorStop(0, "rgba(46, 196, 166, 0.14)");
    g2.addColorStop(1, "transparent");
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, W, H);

    const g3 = ctx.createRadialGradient(
      W * (0.9 + Math.sin(t * 0.1) * 0.05), H * 0.05,
      0, W, 0, W * 0.5
    );
    g3.addColorStop(0, "rgba(244, 185, 66, 0.10)");
    g3.addColorStop(1, "transparent");
    ctx.fillStyle = g3;
    ctx.fillRect(0, 0, W, H);

    if (!reduce) {
      for (const o of orbs) {
        o.y += o.vy * 0.016;
        o.x += o.vx * 0.016 + Math.sin(t + o.spin) * 0.15;
        o.spin += o.spinSp * 0.016;
        if (o.y - o.r > H + 20) spawn(o, false);
        ctx.save();
        ctx.translate(o.x, o.y);
        ctx.rotate(o.spin);
        const rg = ctx.createRadialGradient(0, 0, 0, 0, 0, o.r);
        const [r, g, b] = o.c;
        rg.addColorStop(0, `rgba(${r},${g},${b},${o.a})`);
        rg.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = rg;
        ctx.beginPath();
        ctx.ellipse(0, 0, o.r, o.r * 0.62, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }

    raf = requestAnimationFrame(paint);
  }

  resize();
  t0 = performance.now();
  window.addEventListener("resize", resize, { passive: true });
  if (reduce) {
    paint(t0);
  } else {
    raf = requestAnimationFrame(paint);
  }
  window.addEventListener("pagehide", () => cancelAnimationFrame(raf), { once: true });
})();
