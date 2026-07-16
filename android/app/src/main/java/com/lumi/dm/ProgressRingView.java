package com.lumi.dm;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.util.AttributeSet;
import android.view.View;

public class ProgressRingView extends View {

    private final Paint trackPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint fillPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF oval       = new RectF();

    private int    progress  = 0;
    private String icon      = "⏸";

    public ProgressRingView(Context ctx)                        { this(ctx, null); }
    public ProgressRingView(Context ctx, AttributeSet attrs)    { this(ctx, attrs, 0); }
    public ProgressRingView(Context ctx, AttributeSet attrs, int def) {
        super(ctx, attrs, def);

        float stroke = dp(3.5f);

        trackPaint.setStyle(Paint.Style.STROKE);
        trackPaint.setStrokeWidth(stroke);
        trackPaint.setColor(0x22FFFFFF);

        fillPaint.setStyle(Paint.Style.STROKE);
        fillPaint.setStrokeWidth(stroke);
        fillPaint.setStrokeCap(Paint.Cap.ROUND);
        fillPaint.setColor(0xFF4f9ef8);

        textPaint.setTextAlign(Paint.Align.CENTER);
        textPaint.setColor(0xCCFFFFFF);
    }

    public void setProgress(int pct) {
        progress = Math.max(0, Math.min(100, pct));
        invalidate();
    }

    public void setRingColor(int color) {
        fillPaint.setColor(color);
        invalidate();
    }

    public void setIcon(String ch) {
        icon = ch;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float cx = getWidth() / 2f;
        float cy = getHeight() / 2f;
        float stroke = dp(3.5f);
        float r = Math.min(cx, cy) - stroke - dp(1f);

        oval.set(cx - r, cy - r, cx + r, cy + r);
        canvas.drawOval(oval, trackPaint);

        if (progress > 0) {
            canvas.drawArc(oval, -90, 360f * progress / 100f, false, fillPaint);
        }

        textPaint.setTextSize(r * 0.56f);
        float textY = cy - (textPaint.ascent() + textPaint.descent()) / 2f;
        canvas.drawText(icon, cx, textY, textPaint);
    }

    private float dp(float v) {
        return v * getContext().getResources().getDisplayMetrics().density;
    }
}
