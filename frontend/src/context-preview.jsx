import React from 'react';
import {createRoot} from 'react-dom/client';
import {App} from './main.jsx';

// The production desk itself, backed by a read-only historical API for layout review.
createRoot(document.getElementById('root')).render(<App preview/>);
