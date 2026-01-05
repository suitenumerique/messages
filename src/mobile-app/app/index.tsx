import React from 'react';
import { SafeAreaView  } from 'react-native-safe-area-context';
import { StyleSheet } from 'react-native';
import { WebView } from 'react-native-webview';

export default function App() {
    return (
        <SafeAreaView style={styles.container}>
            <WebView source={{ uri: 'https://messages.sardinepq.fr' }} />
        </SafeAreaView>
    );
}

const styles = StyleSheet.create({
    container: {
        flex: 1,
    },
});
