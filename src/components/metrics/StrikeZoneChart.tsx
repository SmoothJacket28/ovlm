/**
 * Strike zone plot — where the pitch crosses the front of the plate.
 *
 * SVG coordinate system: origin = center of the zone, +x = catcher's right
 * (1 SVG unit = 1 foot). Zone box uses the rule-of-thumb MLB dimensions:
 * 17 in wide, 1.5–3.5 ft off the ground.
 */

import React from 'react';

const ZONE_WIDTH_FT  = 17 / 12;
const ZONE_BOTTOM_FT = 1.5;
const ZONE_TOP_FT    = 3.5;
const PLOT_HALF_FT   = 1.6; // view extends a bit past the zone for balls/near-misses

export function StrikeZoneChart({ location }: { location: { xFt: number; yFt: number } | null }): React.ReactElement {
  const zoneLeft   = -ZONE_WIDTH_FT / 2;
  const zoneRight  =  ZONE_WIDTH_FT / 2;
  const zoneMidY   = (ZONE_BOTTOM_FT + ZONE_TOP_FT) / 2;

  // SVG y grows downward — flip height so "up" on screen is "up" in the zone
  const toSvgY = (yFt: number) => zoneMidY - yFt + zoneMidY;

  const inZone = location != null
    && location.xFt >= zoneLeft && location.xFt <= zoneRight
    && location.yFt >= ZONE_BOTTOM_FT && location.yFt <= ZONE_TOP_FT;

  return (
    <div style={styles.wrap}>
      <div style={styles.header}>
        <span style={styles.title}>STRIKE ZONE</span>
        {location && (
          <span style={{ ...styles.sub, color: inZone ? '#44ff88' : '#ff6688' }}>
            {inZone ? 'STRIKE' : 'BALL'}
          </span>
        )}
      </div>

      <svg
        viewBox={`${-PLOT_HALF_FT} ${zoneMidY - PLOT_HALF_FT - 1} ${PLOT_HALF_FT * 2} ${PLOT_HALF_FT * 2 + 1}`}
        style={styles.svg}
        aria-label="Strike zone plot"
      >
        <rect x={-PLOT_HALF_FT} y={zoneMidY - PLOT_HALF_FT - 1} width={PLOT_HALF_FT * 2} height={PLOT_HALF_FT * 2 + 1} fill="#0a0a0f" />

        {/* Zone box, split into a 3x3 grid like a broadcast strike-zone graphic */}
        {[0, 1, 2, 3].map((i) => (
          <line key={`v${i}`}
            x1={zoneLeft + (ZONE_WIDTH_FT / 3) * i} x2={zoneLeft + (ZONE_WIDTH_FT / 3) * i}
            y1={toSvgY(ZONE_TOP_FT)} y2={toSvgY(ZONE_BOTTOM_FT)}
            stroke="#2a3a2a" strokeWidth={i === 0 || i === 3 ? 1.5 : 0.5}
          />
        ))}
        {[0, 1, 2, 3].map((i) => (
          <line key={`h${i}`}
            x1={zoneLeft} x2={zoneRight}
            y1={toSvgY(ZONE_BOTTOM_FT + ((ZONE_TOP_FT - ZONE_BOTTOM_FT) / 3) * i)}
            y2={toSvgY(ZONE_BOTTOM_FT + ((ZONE_TOP_FT - ZONE_BOTTOM_FT) / 3) * i)}
            stroke="#2a3a2a" strokeWidth={i === 0 || i === 3 ? 1.5 : 0.5}
          />
        ))}

        {/* Pitch location */}
        {location ? (
          <circle
            cx={location.xFt} cy={toSvgY(location.yFt)} r={0.12}
            fill={inZone ? '#44ff88' : '#ff6688'}
            stroke="#0a0a0f" strokeWidth={0.03}
          />
        ) : (
          <text x={0} y={toSvgY(zoneMidY)} fontSize={0.16} fill="#334" textAnchor="middle" fontFamily="inherit">
            no pitch-location data yet
          </text>
        )}
      </svg>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    background: '#080810',
    border: '1px solid #1a1a2e',
    borderRadius: 8,
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
  },
  header: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 8,
    padding: '8px 12px 0',
  },
  title: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.15em',
    color: '#445',
  },
  sub: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
  },
  svg: {
    display: 'block',
    width: '100%',
    maxWidth: 260,
    margin: '8px auto 12px',
    fontFamily: "'JetBrains Mono','Fira Code','SF Mono','Menlo','Consolas',monospace",
  },
};
