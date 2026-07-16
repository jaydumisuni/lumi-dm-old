package com.lumi.dm;

import android.os.Bundle;
import android.text.TextUtils;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.RadioGroup;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import com.lumi.dm.engine.DownloadEngine;
import com.lumi.dm.engine.VideoExtractor;

import java.util.ArrayList;
import java.util.List;

public class NewDownloadFragment extends Fragment {

    private EditText      etUrl, etFilename;
    private RadioGroup    rgType;
    private LinearLayout  llVideoSection;
    private Spinner       spinnerQuality;
    private Button        btnAnalyse, btnDownload;
    private ProgressBar   pbAnalyse;
    private TextView      tvError;

    private final List<VideoExtractor.VideoFormat> formats = new ArrayList<>();

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater,
                             @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View v = inflater.inflate(R.layout.fragment_new_download, container, false);

        etUrl         = v.findViewById(R.id.et_url);
        etFilename    = v.findViewById(R.id.et_filename);
        rgType        = v.findViewById(R.id.rg_type);
        llVideoSection = v.findViewById(R.id.ll_video_section);
        spinnerQuality = v.findViewById(R.id.spinner_quality);
        btnAnalyse    = v.findViewById(R.id.btn_analyse);
        btnDownload   = v.findViewById(R.id.btn_download);
        pbAnalyse     = v.findViewById(R.id.pb_analyse);
        tvError       = v.findViewById(R.id.tv_error);

        rgType.setOnCheckedChangeListener((group, checkedId) ->
                llVideoSection.setVisibility(
                        checkedId == R.id.rb_video ? View.VISIBLE : View.GONE));

        btnAnalyse.setOnClickListener(x -> analyseVideo());
        btnDownload.setOnClickListener(x -> startDownload());

        // Pre-fill URL if passed via arguments (share intent, ACTION_VIEW, or deep link)
        Bundle args = getArguments();
        if (args != null) {
            String sharedUrl = args.getString("url");
            if (!TextUtils.isEmpty(sharedUrl)) {
                etUrl.setText(sharedUrl);
                if (args.getBoolean("auto_video", false)) {
                    // Video platform URL — switch to video mode and start analysis immediately
                    rgType.check(R.id.rb_video);
                    llVideoSection.setVisibility(View.VISIBLE);
                    // Post so views are fully laid out before we trigger analysis
                    etUrl.post(this::analyseVideo);
                }
            }
        }

        return v;
    }

    private void analyseVideo() {
        String url = etUrl.getText().toString().trim();
        if (TextUtils.isEmpty(url)) {
            showError("Enter a URL first");
            return;
        }
        tvError.setVisibility(View.GONE);
        pbAnalyse.setVisibility(View.VISIBLE);
        btnAnalyse.setEnabled(false);
        formats.clear();

        VideoExtractor.extractFormats(url, new VideoExtractor.Callback() {
            @Override
            public void onFormats(List<VideoExtractor.VideoFormat> fmts, String title) {
                if (getActivity() == null) return;
                getActivity().runOnUiThread(() -> {
                    pbAnalyse.setVisibility(View.GONE);
                    btnAnalyse.setEnabled(true);
                    formats.addAll(fmts);

                    List<String> labels = new ArrayList<>();
                    for (VideoExtractor.VideoFormat f : fmts) labels.add(f.label);
                    ArrayAdapter<String> a = new ArrayAdapter<>(
                            requireContext(),
                            android.R.layout.simple_spinner_item, labels);
                    a.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
                    spinnerQuality.setAdapter(a);

                    if (!title.isEmpty() && TextUtils.isEmpty(etFilename.getText()))
                        etFilename.setText(sanitise(title) + ".mp4");
                });
            }

            @Override
            public void onError(String message) {
                if (getActivity() == null) return;
                getActivity().runOnUiThread(() -> {
                    pbAnalyse.setVisibility(View.GONE);
                    btnAnalyse.setEnabled(true);
                    showError("Could not extract video: " + message);
                });
            }
        });
    }

    private void startDownload() {
        String url = etUrl.getText().toString().trim();
        if (TextUtils.isEmpty(url)) {
            Toast.makeText(getContext(), "Enter a URL", Toast.LENGTH_SHORT).show();
            return;
        }

        String  filename = etFilename.getText().toString().trim();
        boolean isVideo  = rgType.getCheckedRadioButtonId() == R.id.rb_video;

        if (isVideo) {
            if (formats.isEmpty()) {
                Toast.makeText(getContext(), "Tap \"Analyse\" first", Toast.LENGTH_SHORT).show();
                return;
            }
            int idx = spinnerQuality.getSelectedItemPosition();
            if (idx >= 0 && idx < formats.size()) {
                VideoExtractor.VideoFormat fmt = formats.get(idx);
                if (filename.isEmpty()) filename = sanitise(fmt.quality) + ".mp4";
                DownloadEngine.get().addVideo(fmt.url, filename, null);
            }
        } else {
            DownloadEngine.get().addHttp(url, filename.isEmpty() ? null : filename, null);
        }

        Toast.makeText(getContext(), "Download queued", Toast.LENGTH_SHORT).show();
        etUrl.setText("");
        etFilename.setText("");
        formats.clear();
        if (spinnerQuality.getAdapter() != null)
            spinnerQuality.setAdapter(null);

        if (getActivity() instanceof MainActivity)
            ((MainActivity) getActivity()).switchToDownloads();
    }

    private void showError(String msg) {
        tvError.setText(msg);
        tvError.setVisibility(View.VISIBLE);
    }

    private static String sanitise(String name) {
        return name.replaceAll("[\\\\/:*?\"<>|]", "_").trim();
    }
}
