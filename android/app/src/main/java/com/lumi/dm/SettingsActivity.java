package com.lumi.dm;

import android.os.Bundle;

import androidx.appcompat.app.AppCompatActivity;

// Settings are now handled by SettingsFragment inside MainActivity.
// This class is kept only to avoid breaking any lingering intent references.
public class SettingsActivity extends AppCompatActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        finish();   // forward to MainActivity which hosts SettingsFragment
    }
}
