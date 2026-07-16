package com.lumi.dm;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import com.lumi.dm.engine.DownloadEngine;

public class SettingsFragment extends Fragment {

    private TextView tvFolder, tvMaxConc, tvConnections;
    private SeekBar  sbMaxConc, sbConnections;

    private final ActivityResultLauncher<Uri> folderPicker =
            registerForActivityResult(new ActivityResultContracts.OpenDocumentTree(), uri -> {
                if (uri == null || getContext() == null) return;
                try {
                    getContext().getContentResolver().takePersistableUriPermission(uri,
                            Intent.FLAG_GRANT_READ_URI_PERMISSION
                            | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                } catch (Exception ignored) {}

                // Convert content URI to a real file path where possible
                String path = uriToPath(uri);
                DownloadEngine.get().setDefaultDir(path);
                tvFolder.setText(path);
                Toast.makeText(getContext(), "Folder saved", Toast.LENGTH_SHORT).show();
            });

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater,
                             @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View v = inflater.inflate(R.layout.fragment_settings, container, false);

        tvFolder      = v.findViewById(R.id.tv_folder);
        tvMaxConc     = v.findViewById(R.id.tv_max_conc);
        sbMaxConc     = v.findViewById(R.id.sb_max_conc);
        tvConnections = v.findViewById(R.id.tv_connections);
        sbConnections = v.findViewById(R.id.sb_connections);
        Button btnPickFolder = v.findViewById(R.id.btn_pick_folder);

        tvFolder.setText(DownloadEngine.get().getDefaultDir());

        int cur = DownloadEngine.get().getMaxConcurrent();
        sbMaxConc.setMax(7);   // range 1–8
        sbMaxConc.setProgress(cur - 1);
        tvMaxConc.setText(cur + " simultaneous");

        int curConns = DownloadEngine.get().getDefaultConnections();
        sbConnections.setMax(63);  // range 1–64
        sbConnections.setProgress(curConns - 1);
        tvConnections.setText(curConns + " connections");

        btnPickFolder.setOnClickListener(x -> folderPicker.launch(null));

        sbMaxConc.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar sb, int progress, boolean fromUser) {
                int val = progress + 1;
                tvMaxConc.setText(val + " simultaneous");
                if (fromUser) DownloadEngine.get().setMaxConcurrent(val);
            }
            @Override public void onStartTrackingTouch(SeekBar sb) {}
            @Override public void onStopTrackingTouch(SeekBar sb) {}
        });

        sbConnections.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar sb, int progress, boolean fromUser) {
                int val = progress + 1;
                tvConnections.setText(val + " connections");
                if (fromUser) DownloadEngine.get().setDefaultConnections(val);
            }
            @Override public void onStartTrackingTouch(SeekBar sb) {}
            @Override public void onStopTrackingTouch(SeekBar sb) {}
        });

        return v;
    }

    private String uriToPath(Uri uri) {
        String uriStr = uri.toString();
        // content://com.android.externalstorage.documents/tree/primary:Download
        if (uriStr.contains("primary:")) {
            String relative = uriStr.substring(uriStr.indexOf("primary:") + 8);
            try { relative = java.net.URLDecoder.decode(relative, "UTF-8"); } catch (Exception ignored) {}
            return Environment.getExternalStorageDirectory().getAbsolutePath()
                    + (relative.isEmpty() ? "" : "/" + relative);
        }
        // Fall back to the URI string representation
        return uri.getPath() != null ? uri.getPath() : uriStr;
    }
}
